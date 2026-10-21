"""把 Mem0 命中和 Graphiti 节点/边收成 MemoryItem。

层级半径与 canned.py 一致：ROLE 0.05、RULE 0.30、PRINCIPLE 0.55、EPISODE 0.90。
parent 链：role ← rule ← principle ← episode。RecallDepth 与 top_k 的过滤也与 canned 相同。
"""
from __future__ import annotations

import time
from datetime import datetime

from termitaria.memory.v1 import memory_pb2

ROLE_RADIUS = 0.05
RULE_RADIUS = 0.30
PRINCIPLE_RADIUS = 0.55
EPISODE_RADIUS = 0.90

_ROLE_TEXT = "You are a researcher in a Termitaria swarm."
_RULE_TEXT = "Cite your sources. When you are unsure, say so."

_PRINCIPLE_SIDE = frozenset(
    (
        memory_pb2.MEMORY_KIND_ROLE,
        memory_pb2.MEMORY_KIND_RULE,
        memory_pb2.MEMORY_KIND_PRINCIPLE,
    )
)


def role_id(agent_id: str) -> str:
    return f"mi_{agent_id}_role"


def rule_id(agent_id: str) -> str:
    return f"mi_{agent_id}_rule"


def principle_id(agent_id: str, node_id: str) -> str:
    return f"mi_{agent_id}_principle_{node_id}"


def seeds(agent_id: str) -> list[memory_pb2.MemoryItem]:
    role = memory_pb2.MemoryItem(
        item_id=role_id(agent_id),
        agent_id=agent_id,
        kind=memory_pb2.MEMORY_KIND_ROLE,
        text=_ROLE_TEXT,
        score=1.0 - ROLE_RADIUS,
        poincare_radius=ROLE_RADIUS,
        parent_id="",
    )
    rule = memory_pb2.MemoryItem(
        item_id=rule_id(agent_id),
        agent_id=agent_id,
        kind=memory_pb2.MEMORY_KIND_RULE,
        text=_RULE_TEXT,
        score=1.0 - RULE_RADIUS,
        poincare_radius=RULE_RADIUS,
        parent_id=role.item_id,
    )
    return [role, rule]


def mem0_items(agent_id: str, hits: list[dict]) -> list[memory_pb2.MemoryItem]:
    items = seeds(agent_id)
    parent = rule_id(agent_id)
    for hit in hits:
        text = str(hit.get("memory") or hit.get("text") or "").strip()
        if not text:
            continue
        meta = hit.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        run_id = str(hit.get("run_id") or meta.get("run_id") or "")
        items.append(
            memory_pb2.MemoryItem(
                item_id=str(hit.get("id") or f"mi_{agent_id}_episode_{len(items)}"),
                agent_id=agent_id,
                kind=memory_pb2.MEMORY_KIND_EPISODE,
                text=text,
                score=float(hit.get("score") or 0.0),
                poincare_radius=EPISODE_RADIUS,
                parent_id=parent,
                occurred_at_ms=_ms(meta.get("occurred_at_ms")),
                source_session_id=run_id,
            )
        )
    return items


def graphiti_items(agent_id: str, nodes: list, edges: list) -> list[memory_pb2.MemoryItem]:
    items = seeds(agent_id)
    rule = rule_id(agent_id)
    for node in nodes:
        node_id = str(_field(node, "uuid") or _field(node, "id") or "")
        text = str(_field(node, "summary") or _field(node, "name") or "").strip()
        if not node_id or not text:
            continue
        items.append(
            memory_pb2.MemoryItem(
                item_id=principle_id(agent_id, node_id),
                agent_id=agent_id,
                kind=memory_pb2.MEMORY_KIND_PRINCIPLE,
                text=text,
                score=float(_field(node, "score") or (1.0 - PRINCIPLE_RADIUS)),
                poincare_radius=PRINCIPLE_RADIUS,
                parent_id=rule,
            )
        )
    for edge in edges:
        fact = str(_field(edge, "fact") or _field(edge, "text") or "").strip()
        if not fact:
            continue
        edge_id = str(_field(edge, "uuid") or _field(edge, "id") or len(items))
        source = str(_field(edge, "source_node_uuid") or "")
        parent = principle_id(agent_id, source) if source else rule
        items.append(
            memory_pb2.MemoryItem(
                item_id=f"mi_{agent_id}_episode_{edge_id}",
                agent_id=agent_id,
                kind=memory_pb2.MEMORY_KIND_EPISODE,
                text=fact,
                score=float(_field(edge, "score") or 0.0),
                poincare_radius=EPISODE_RADIUS,
                parent_id=parent,
                occurred_at_ms=_ms(_field(edge, "valid_at")),
            )
        )
    return items


def assemble_response(
    req: memory_pb2.RecallRequest,
    items: list[memory_pb2.MemoryItem],
    *,
    degraded: bool = False,
    started: float | None = None,
) -> memory_pb2.RecallResponse:
    began = time.time() if started is None else started
    if req.depth == memory_pb2.RECALL_DEPTH_PRINCIPLES_ONLY:
        items = [it for it in items if it.kind in _PRINCIPLE_SIDE]
    elif req.depth == memory_pb2.RECALL_DEPTH_EPISODES_ONLY:
        items = [it for it in items if it.kind == memory_pb2.MEMORY_KIND_EPISODE]
    candidate_count = len(items)
    top_k = req.top_k or 5
    items = items[:top_k]
    return memory_pb2.RecallResponse(
        items=items,
        meta=memory_pb2.RecallMeta(
            candidate_count=candidate_count,
            reranked_count=len(items),
            latency_ms=max(0, int((time.time() - began) * 1000)),
            degraded=degraded,
        ),
    )


def _field(obj, name: str):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _ms(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, (int, float)):
        return int(value)
    return 0
