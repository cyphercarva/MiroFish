"""
Local graph helpers used when Zep is unavailable.
"""

import json
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from ..config import Config
from .zep_entity_reader import EntityNode, FilteredEntities


LOCAL_GRAPH_PREFIX = "local_"

DEFAULT_TOPICS = [
    "public opinion",
    "social media",
    "community",
    "response",
]

DEFAULT_ENTITY_TYPES = [
    {
        "name": "PublicFigure",
        "description": "Visible spokesperson or influential public actor.",
        "attributes": [{"name": "role", "type": "text", "description": "Public role"}],
        "examples": ["Lead Spokesperson"],
    },
    {
        "name": "Journalist",
        "description": "Media professional reporting and amplifying events.",
        "attributes": [{"name": "beat", "type": "text", "description": "Coverage area"}],
        "examples": ["Breaking News Desk"],
    },
    {
        "name": "MediaOutlet",
        "description": "Official media account or publication.",
        "attributes": [{"name": "focus_area", "type": "text", "description": "Coverage focus"}],
        "examples": ["Daily Observer"],
    },
    {
        "name": "GovernmentAgency",
        "description": "Institutional account issuing statements and responses.",
        "attributes": [{"name": "jurisdiction", "type": "text", "description": "Scope of authority"}],
        "examples": ["City Response Office"],
    },
    {
        "name": "Company",
        "description": "Business entity involved in the discussion.",
        "attributes": [{"name": "industry", "type": "text", "description": "Business domain"}],
        "examples": ["Example Corp"],
    },
    {
        "name": "CommunityGroup",
        "description": "Organized user group or community voice.",
        "attributes": [{"name": "community_type", "type": "text", "description": "Community segment"}],
        "examples": ["Concerned Residents"],
    },
    {
        "name": "Person",
        "description": "Any individual person not fitting a more specific type.",
        "attributes": [{"name": "full_name", "type": "text", "description": "Display name"}],
        "examples": ["Local Resident"],
    },
    {
        "name": "Organization",
        "description": "Any organization not fitting a more specific type.",
        "attributes": [{"name": "org_name", "type": "text", "description": "Organization name"}],
        "examples": ["Civic Association"],
    },
]

DEFAULT_EDGE_TYPES = [
    {
        "name": "REPORTS_ON",
        "description": "Covers or publishes updates about another actor.",
    },
    {
        "name": "RESPONDS_TO",
        "description": "Issues a response to another actor or statement.",
    },
    {
        "name": "SUPPORTS",
        "description": "Publicly supports another actor or initiative.",
    },
    {
        "name": "OPPOSES",
        "description": "Publicly opposes another actor or initiative.",
    },
    {
        "name": "COLLABORATES_WITH",
        "description": "Coordinates or works together with another actor.",
    },
]


def is_local_graph(graph_id: Optional[str]) -> bool:
    return bool(graph_id and graph_id.startswith(LOCAL_GRAPH_PREFIX))


def make_local_graph_id(project_id: str) -> str:
    return f"{LOCAL_GRAPH_PREFIX}{project_id}"


def get_project_id_from_graph_id(graph_id: str) -> str:
    if not is_local_graph(graph_id):
        raise ValueError(f"Not a local graph id: {graph_id}")
    return graph_id[len(LOCAL_GRAPH_PREFIX):]


def get_local_graph_path(project_id: str) -> str:
    return os.path.join(Config.UPLOAD_FOLDER, "projects", project_id, "local_graph.json")


def save_local_graph(project_id: str, graph_data: Dict[str, Any]) -> None:
    graph_path = get_local_graph_path(project_id)
    os.makedirs(os.path.dirname(graph_path), exist_ok=True)
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(graph_data, f, ensure_ascii=False, indent=2)


def load_local_graph(graph_id: str) -> Dict[str, Any]:
    project_id = get_project_id_from_graph_id(graph_id)
    graph_path = get_local_graph_path(project_id)

    if not os.path.exists(graph_path):
        raise FileNotFoundError(f"Local graph not found for {project_id}")

    with open(graph_path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_local_graph(graph_id: str) -> None:
    project_id = get_project_id_from_graph_id(graph_id)
    graph_path = get_local_graph_path(project_id)
    if os.path.exists(graph_path):
        os.remove(graph_path)


def build_local_graph(
    project_id: str,
    project_name: str,
    text: str,
    ontology: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    topics = _extract_topics(text)
    entity_types = (ontology or {}).get("entity_types") or DEFAULT_ENTITY_TYPES
    edge_types = (ontology or {}).get("edge_types") or DEFAULT_EDGE_TYPES
    graph_id = make_local_graph_id(project_id)

    nodes = _build_nodes(project_name, entity_types, topics, graph_id)
    edges = _build_edges(nodes, edge_types, graph_id)

    return {
        "graph_id": graph_id,
        "project_id": project_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "topics": topics,
        "nodes": nodes,
        "edges": edges,
    }


def get_local_filtered_entities(
    graph_id: str,
    defined_entity_types: Optional[List[str]] = None,
    enrich_with_edges: bool = True
) -> FilteredEntities:
    graph_data = load_local_graph(graph_id)
    all_nodes = graph_data.get("nodes", [])
    all_edges = graph_data.get("edges", []) if enrich_with_edges else []
    node_map = {node["uuid"]: node for node in all_nodes}

    entities: List[EntityNode] = []
    entity_types = set()

    for node in all_nodes:
        labels = node.get("labels", [])
        custom_labels = [label for label in labels if label not in ["Entity", "Node"]]

        if not custom_labels:
            continue

        entity_type = custom_labels[0]
        if defined_entity_types and entity_type not in defined_entity_types:
            continue

        entity_types.add(entity_type)
        entity = EntityNode(
            uuid=node.get("uuid", ""),
            name=node.get("name", ""),
            labels=labels,
            summary=node.get("summary", ""),
            attributes=node.get("attributes", {}),
        )

        if enrich_with_edges:
            related_edges = []
            related_node_uuids = set()

            for edge in all_edges:
                if edge.get("source_node_uuid") == entity.uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge.get("name", ""),
                        "fact": edge.get("fact", ""),
                        "target_node_uuid": edge.get("target_node_uuid"),
                    })
                    related_node_uuids.add(edge.get("target_node_uuid"))
                elif edge.get("target_node_uuid") == entity.uuid:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge.get("name", ""),
                        "fact": edge.get("fact", ""),
                        "source_node_uuid": edge.get("source_node_uuid"),
                    })
                    related_node_uuids.add(edge.get("source_node_uuid"))

            entity.related_edges = related_edges
            entity.related_nodes = [
                {
                    "uuid": node_map[related_uuid].get("uuid", ""),
                    "name": node_map[related_uuid].get("name", ""),
                    "labels": node_map[related_uuid].get("labels", []),
                    "summary": node_map[related_uuid].get("summary", ""),
                }
                for related_uuid in related_node_uuids
                if related_uuid in node_map
            ]

        entities.append(entity)

    return FilteredEntities(
        entities=entities,
        entity_types=entity_types,
        total_count=len(all_nodes),
        filtered_count=len(entities),
    )


def _build_nodes(
    project_name: str,
    entity_types: List[Dict[str, Any]],
    topics: List[str],
    graph_id: str
) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    seen_names = set()

    for index, entity_type in enumerate(entity_types[:10]):
        type_name = entity_type.get("name", f"EntityType{index + 1}")
        name = _pick_node_name(project_name, entity_type, topics, index)
        summary = entity_type.get("description", "") or f"{name} participates in the discussion."
        attributes = _build_attributes(entity_type, name, topics)
        node_name = _dedupe_name(name, seen_names)

        nodes.append({
            "uuid": f"{graph_id}_node_{index + 1}",
            "name": node_name,
            "labels": ["Entity", type_name],
            "summary": summary,
            "attributes": attributes,
        })

    while len(nodes) < 4:
        idx = len(nodes) + 1
        node_name = _dedupe_name(f"{project_name} Agent {idx}", seen_names)
        nodes.append({
            "uuid": f"{graph_id}_node_{idx}",
            "name": node_name,
            "labels": ["Entity", "Person"],
            "summary": "Fallback local entity for demo mode.",
            "attributes": {"role": "participant"},
        })

    return nodes


def _build_edges(
    nodes: List[Dict[str, Any]],
    edge_types: List[Dict[str, Any]],
    graph_id: str
) -> List[Dict[str, Any]]:
    if len(nodes) < 2:
        return []

    safe_edge_types = edge_types or DEFAULT_EDGE_TYPES
    edges: List[Dict[str, Any]] = []

    for index in range(len(nodes) - 1):
        edge_type = safe_edge_types[index % len(safe_edge_types)]
        source = nodes[index]
        target = nodes[index + 1]
        edge_name = edge_type.get("name", "RELATED_TO")
        edge_desc = edge_type.get("description", "is related to")
        edges.append({
            "uuid": f"{graph_id}_edge_{index + 1}",
            "name": edge_name,
            "fact_type": edge_name,
            "fact": f"{source['name']} {edge_desc.lower()} {target['name']}",
            "source_node_uuid": source["uuid"],
            "target_node_uuid": target["uuid"],
            "attributes": {},
        })

    if len(nodes) > 2:
        source = nodes[-1]
        target = nodes[0]
        edge_type = safe_edge_types[len(edges) % len(safe_edge_types)]
        edge_name = edge_type.get("name", "RELATED_TO")
        edge_desc = edge_type.get("description", "is related to")
        edges.append({
            "uuid": f"{graph_id}_edge_{len(edges) + 1}",
            "name": edge_name,
            "fact_type": edge_name,
            "fact": f"{source['name']} {edge_desc.lower()} {target['name']}",
            "source_node_uuid": source["uuid"],
            "target_node_uuid": target["uuid"],
            "attributes": {},
        })

    return edges


def _pick_node_name(
    project_name: str,
    entity_type: Dict[str, Any],
    topics: List[str],
    index: int
) -> str:
    examples = [example for example in entity_type.get("examples", []) if isinstance(example, str) and example.strip()]
    if examples:
        return examples[0].strip()

    type_name = entity_type.get("name", f"Entity{index + 1}")
    if topics:
        return f"{topics[index % len(topics)].title()} {type_name}"

    return f"{project_name} {type_name}"


def _build_attributes(
    entity_type: Dict[str, Any],
    node_name: str,
    topics: List[str]
) -> Dict[str, Any]:
    attributes: Dict[str, Any] = {}
    focus_topic = topics[0] if topics else "general discussion"

    for attribute in entity_type.get("attributes", [])[:3]:
        attr_name = attribute.get("name")
        if not attr_name:
            continue

        attr_name_lower = attr_name.lower()
        if "name" in attr_name_lower:
            value = node_name
        elif "role" in attr_name_lower:
            value = entity_type.get("name", "participant")
        elif "topic" in attr_name_lower or "focus" in attr_name_lower:
            value = focus_topic
        elif "country" in attr_name_lower or "location" in attr_name_lower:
            value = "Local"
        else:
            value = f"{focus_topic} profile"

        attributes[attr_name] = value

    return attributes


def _dedupe_name(name: str, seen_names: set[str]) -> str:
    if name not in seen_names:
        seen_names.add(name)
        return name

    suffix = 2
    while f"{name} {suffix}" in seen_names:
        suffix += 1

    unique_name = f"{name} {suffix}"
    seen_names.add(unique_name)
    return unique_name


def _extract_topics(text: str, limit: int = 6) -> List[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z\\-]{3,}|[\u4e00-\u9fff]{2,8}", text or "")
    cleaned = []

    stopwords = {
        "this", "that", "with", "from", "were", "have", "about", "their",
        "there", "would", "could", "should", "into", "between", "through",
        "project", "simulation", "report", "analysis", "entity", "graph",
    }

    for token in tokens:
        normalized = token.strip().lower()
        if normalized in stopwords:
            continue
        if normalized.isdigit():
            continue
        cleaned.append(normalized)

    common = [token for token, _count in Counter(cleaned).most_common(limit)]
    return common or DEFAULT_TOPICS[:]
