"""把一个 agent 的图收成网页用的 JSON。不连数据库。"""
from __future__ import annotations

import re

_NODE_CAP = 80
_AGENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def agent_ok(agent_id: str) -> bool:
    return bool(_AGENT.fullmatch(agent_id))


def empty_graph_view(backend: str, agent_id: str) -> dict:
    return build_graph_view(backend, agent_id, [], [])


def build_graph_view(
    backend: str,
    agent_id: str,
    node_rows: list[dict],
    edge_rows: list[dict],
) -> dict:
    nodes = []
    seen: set[str] = set()
    for row in node_rows:
        node_id = _text(row.get("id"))
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "name": _text(row.get("name")) or node_id,
                "summary": _text(row.get("summary")),
            }
        )
        if len(nodes) >= _NODE_CAP:
            break
    allowed = {node["id"] for node in nodes}
    edges = []
    for row in edge_rows:
        source = _text(row.get("source"))
        target = _text(row.get("target"))
        if source not in allowed or target not in allowed:
            continue
        fact = _text(row.get("fact"))
        name = _text(row.get("name"))
        if not fact and not name:
            continue
        invalid_at = _text(row.get("invalid_at"))
        edges.append(
            {
                "id": _text(row.get("id")) or f"{source}:{target}:{len(edges)}",
                "source": source,
                "target": target,
                "fact": fact or name,
                "name": name,
                "valid_at": _text(row.get("valid_at")),
                "invalid_at": invalid_at,
                "expired": bool(invalid_at),
            }
        )
    return {
        "backend": backend,
        "agent_id": agent_id,
        "nodes": nodes,
        "edges": edges,
    }


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()
