"""罐装记忆：让层级字段（poincare_radius / parent_id）在真内核落地前就可测。

同事接手时替换本文件的内核：Qdrant 欧氏候选 → Poincaré 距离精排（contracts §3.3）。
接口保持不变——RecallResponse 的形状、degraded 降级语义都不变。
"""
from __future__ import annotations

import time

from termitaria.memory.v1 import memory_pb2

# 罐装条目模板：(kind, radius, 文本)。radius 从球心到球缘递增（架构 §5.1）。
# parent 链：ROLE ← RULE ← PRINCIPLE ← EPISODE，客户端可重组记忆树（Memory Inspector）。
_CANNED = (
    (memory_pb2.MEMORY_KIND_ROLE, 0.05, "你是 Termitaria swarm 中的一名研究员。"),
    (memory_pb2.MEMORY_KIND_RULE, 0.30, "引用必须给出出处；不确定时明确说不确定。"),
    (memory_pb2.MEMORY_KIND_PRINCIPLE, 0.55, "先验证假设，再扩大投入。"),
    (memory_pb2.MEMORY_KIND_EPISODE, 0.90, "上次 session 中关于双曲嵌入层级的讨论纪要。"),
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
            score=1.0 - radius,  # 罐装分：越靠球心越高
            poincare_radius=radius,
            parent_id="",  # 下面按模板顺序串成链
            occurred_at_ms=0,
        )
        for kind, radius, text in _CANNED
    ]
    for prev, cur in zip(items, items[1:]):
        cur.parent_id = prev.item_id
    if req.depth == memory_pb2.RECALL_DEPTH_PRINCIPLES_ONLY:
        items = [it for it in items if it.kind in _PRINCIPLE_SIDE]
    elif req.depth == memory_pb2.RECALL_DEPTH_EPISODES_ONLY:
        items = [it for it in items if it.kind == memory_pb2.MEMORY_KIND_EPISODE]
    # AUTO / UNSPECIFIED：全谱
    candidate_count = len(items)
    top_k = req.top_k or 5
    items = items[:top_k]
    
    return memory_pb2.RecallResponse(
        items=items,
        meta=memory_pb2.RecallMeta(
            candidate_count=candidate_count,
            reranked_count=len(items),
            latency_ms=int((time.time() - started) * 1000),
            degraded=False,  # 真内核里欧氏兜底时置 true，接口不变（架构 §9）
        ),
    )
