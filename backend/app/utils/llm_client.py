"""
LLM客户端封装
统一使用OpenAI格式调用 with JSON mode fallbacks
"""

import json
import re
import logging
from typing import Optional, Dict, Any, List
from openai import OpenAI

from ..config import Config

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM客户端"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        self.is_ollama = Config.is_ollama_base_url(self.base_url)
        
        if not self.is_ollama and not Config.has_valid_llm_api_key(self.api_key, self.base_url):
            raise ValueError("LLM_API_KEY 未配置")
        
        self.client = OpenAI(
            api_key=(self.api_key or 'ollama'),
            base_url=self.base_url
        )
    
    def _detect_provider(self) -> str:
        """
        Detect LLM provider from base_url
        """
        base_url = getattr(self, 'base_url', Config.LLM_BASE_URL or '')
        base_url_lower = base_url.lower()
        if 'groq.com' in base_url_lower:
            return 'groq'
        if 'openai.com' in base_url_lower or 'openrouter.ai' in base_url_lower:
            return 'openai'
        if Config.is_ollama_base_url(base_url):
            return 'ollama'
        return 'unknown'
    
    def _aggressive_clean(self, text: str) -> str:
        """
        Advanced text cleaning for JSON extraction (Fix #180)
        """
        text = text.strip()
        # Remove think tags
        text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)
        # Remove markdown code blocks
        text = re.sub(r'^```(?:json)?\s*\n?', '', text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r'\n?```\s*$', '', text, flags=re.MULTILINE | re.IGNORECASE)
        # Remove common wrappers: 'JSON:', 'Output:', etc.
        text = re.sub(r'^\s*(?:json\s*:?|output\s*:?|result\s*:?)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*(?:\n\s*)*$', '', text)
        return text.strip()
    
    def _extract_json_regex(self, text: str) -> Optional[str]:
        """
        Regex-based JSON extraction as final fallback
        """
        # Match largest valid JSON object (handles nested)
        json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}[^{}]*))*\}'
        matches = re.findall(json_pattern, text, re.DOTALL)
        if matches:
            candidate = max(matches, key=len)  # Largest match
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        return None
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        发送聊天请求
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            response_format: 响应格式（如JSON模式）
            
        Returns:
            模型响应文本
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if response_format:
            kwargs["response_format"] = response_format
        
        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        return content.strip()
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        发送聊天请求并返回JSON with intelligent fallbacks (Fix #180)
        
        Strategy:
        1. OpenAI: Strict JSON mode
        2. Others (Groq/Ollama): Text mode + cleaning
        3. Regex extraction fallback
        4. Provider-specific guidance
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            
        Returns:
            解析后的JSON对象
            
        Raises:
            ValueError: All fallbacks exhausted
        """
        provider = self._detect_provider()
        logger.info(f"[chat_json] Provider: {provider}, Model: {self.model}")
        
        # Step 1: Primary attempt
        try:
            use_json_mode = provider == 'openai'  # Only OpenAI gets strict mode
            if use_json_mode:
                logger.debug("[chat_json] Attempt 1: Strict JSON mode")
                response = self.chat(
                    messages, temperature, max_tokens,
                    response_format={"type": "json_object"}
                )
            else:
                logger.debug("[chat_json] Attempt 1: Text mode (JSON instruction)")
                response = self.chat(messages, temperature, max_tokens)
            
            # Aggressive cleaning
            cleaned = self._aggressive_clean(response)
            parsed = json.loads(cleaned)
            logger.info(f"[chat_json] SUCCESS: {provider} (method: {'strict' if use_json_mode else 'text'})")
            return parsed
            
        except json.JSONDecodeError as e1:
            logger.warning(f"[chat_json] Primary failed ({provider}): {str(e1)[:100]}")
            
            # Step 2: Regex fallback
            json_str = self._extract_json_regex(response)
            if json_str:
                try:
                    parsed = json.loads(json_str)
                    logger.info(f"[chat_json] SUCCESS: {provider} (regex fallback)")
                    return parsed
                except json.JSONDecodeError as e2:
                    logger.warning(f"[chat_json] Regex failed: {str(e2)[:100]}")
            
            # Step 3: Provider advice
            advice = {
                'groq': (
                    "Groq JSON mode inconsistent. Recommendations:\n"
                    "1. Switch to OpenAI: LLM_BASE_URL=https://api.openai.com/v1, LLM_MODEL_NAME=gpt-4o-mini\n"
                    "2. Local Ollama: LLM_BASE_URL=http://localhost:11434/v1, ollama pull llama3.1\n"
                    "3. Add 'Respond with PURE JSON, no markdown or explanations' to system prompt"
                ),
                'ollama': "Run 'ollama serve' and 'ollama pull {model}' first.",
                'unknown': "JSON extraction failed. Use OpenAI-compatible provider."
            }
            detail = advice.get(provider, "All fallbacks exhausted.")
            full_raw = response[:1000] + "..." if len(response) > 1000 else response
            raise ValueError(
                f"chat_json failed for {provider} after fallbacks: {detail}\n"
                f"Raw response: {full_raw}"
            ) from e1
