"""罐装记忆：后端不可用时的固定召回。

接口保持不变——RecallResponse 的形状、degraded 降级语义都不变。
"""
from __future__ import annotations

import time

from termitaria.memory.v1 import memory_pb2

# 不要放一条假的 episode。worker 会把它写进 Remembered facts，模型就会说什么都没记住。
_CANNED = (
    (memory_pb2.MEMORY_KIND_ROLE, "You are a researcher in Termitaria swarm。"),
    (memory_pb2.MEMORY_KIND_RULE, "You must provide evidence for your reference. If you are unsure, state clearly you are unsure."),
    (memory_pb2.MEMORY_KIND_PRINCIPLE, "First validate the hypothesis, then expand the investment."),
)

_PRINCIPLE_SIDE = frozenset(
    (
        memory_pb2.MEMORY_KIND_ROLE,
        memory_pb2.MEMORY_KIND_RULE,
        memory_pb2.MEMORY_KIND_PRINCIPLE,
    )
)

def build_recall_response(req: memory_pb2.RecallRequest) -> memory_pb2.RecallResponse:
    """按 RecallDepth 过滤罐装条目，组装带 meta 的响应（纯函数，可离线测）。"""
    started = time.time()
    items = [
        memory_pb2.MemoryItem(
            item_id=f"mi_{req.agent_id}_{memory_pb2.MemoryKind.Name(kind).lower()}",
            agent_id=req.agent_id,
            kind=kind,
            text=text,
            score=1.0,
            occurred_at_ms=0,
        )
        for kind, text in _CANNED
    ]
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
            latency_ms=int((time.time() - started) * 1000),
            degraded=False,
        ),
    )
