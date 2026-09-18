"""LangGraph worker 图（架构 §4：reason → act → observe 的 MVP 骨架）。

当前三个节点：build_prompt → reason → finalize。
- act / observe（工具调用）是下一刀：TaskDelta.oneof 已带 tool_call，图结构为此预留；
- 记忆不在图里读：TaskContext.recalled / graph_node_ids 由 Go 侧在 dispatch 前预取
  （concepts.md §3：memory recall happens before task dispatch, not during inference）。
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from termitaria.common.v1 import common_pb2
from termitaria.memory.v1 import memory_pb2
from termitaria.session.v1 import session_pb2
from termitaria.task.v1 import task_pb2

from .model import Chunk, Done, ModelPort

EmitDelta = Callable[[task_pb2.TaskDelta], Awaitable[None]]

# role / rules / principle 构成 system prompt（Poincaré 球心侧的上位记忆）
_ROLE_KINDS = (
    memory_pb2.MEMORY_KIND_ROLE,
    memory_pb2.MEMORY_KIND_RULE,
    memory_pb2.MEMORY_KIND_PRINCIPLE,
)


class WorkerState(TypedDict, total=False):
    task: task_pb2.Task
    system: str
    window: list[tuple[str, str]]  # (sender_id, text)
    text: str
    usage: task_pb2.TaskUsage
    error: common_pb2.Error
    cancelled: bool
    seq: int  # 已发出的 delta 序号；text chunk 与收尾帧共用一条序列


def build_graph(model: ModelPort, emit: EmitDelta):
    """构造编译后的图。emit 由 runner 注入（发布到 task.delta_subject）。"""

    async def build_prompt(state: WorkerState) -> dict:
        task = state["task"]
        role_lines = [it.text for it in task.context.recalled if it.kind in _ROLE_KINDS]
        system = "\n".join(role_lines) or (
            f"You are agent '{task.agent_id}' in a Termitaria swarm. "
            "(stub system prompt：MemoryService 未接，role/rules 为空)"
        )
        window = [(_sender(m), _first_text(m)) for m in task.context.window]
        return {"system": system, "window": window}

    async def reason(state: WorkerState) -> dict:
        task = state["task"]
        seq = state.get("seq", 0)
        parts: list[str] = []
        usage = None
        cancelled = False
        try:
            async for ev in model.stream(
                system=state["system"],
                window=state["window"],
                model=task.model,
                max_tokens=task.budget.max_tokens,
            ):
                if _deadline_exceeded(task.budget):
                    cancelled = True
                    break
                if isinstance(ev, Done):
                    usage = ev.usage
                    continue
                parts.append(ev.text)
                seq += 1
                await emit(task_pb2.TaskDelta(task_id=task.task_id, seq=seq, text=ev.text))
        except Exception as exc:  # 模型/网络错误 → FAILED；不抛出（ack 语义见 consumer）
            return {
                "error": common_pb2.Error(code="WORKER_ERROR", message=str(exc), retryable=False),
                "seq": seq,
            }
        out: dict = {"text": "".join(parts), "seq": seq, "cancelled": cancelled}
        if usage is not None:
            out["usage"] = usage
        if cancelled:
            out["error"] = common_pb2.Error(
                code="BUDGET_EXCEEDED", message="deadline_ms exceeded", retryable=False
            )
        return out

    async def finalize(state: WorkerState) -> dict:
        task = state["task"]
        seq = state.get("seq", 0) + 1
        err = state.get("error")
        usage = state.get("usage") or task_pb2.TaskUsage(model=task.model.model or "unknown")
        # 收尾帧二选一：error 优先于 usage（contracts §4：delta 的终态帧）。
        if err is not None and err.code:
            await emit(task_pb2.TaskDelta(task_id=task.task_id, seq=seq, error=err))
        else:
            await emit(task_pb2.TaskDelta(task_id=task.task_id, seq=seq, usage=usage))
        return {"seq": seq, "usage": usage}

    g = StateGraph(WorkerState)
    g.add_node("build_prompt", build_prompt)
    g.add_node("reason", reason)
    g.add_node("finalize", finalize)
    g.add_edge(START, "build_prompt")
    g.add_edge("build_prompt", "reason")
    g.add_edge("reason", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def _deadline_exceeded(budget: common_pb2.Budget) -> bool:
    # common.proto：deadline_ms 是绝对截止时间（epoch ms），0 = 不限。
    # 注意：schemas/swarmspec.schema.json 里 budget.deadlineMs 的注释是「相对毫秒数」——
    # 两侧语义不一致，应由 SwarmSpec loader 在入队时换算成绝对时间（已知契约缺口）。
    return bool(budget.deadline_ms) and time.time() * 1000 > budget.deadline_ms


def _sender(msg: session_pb2.Message) -> str:
    # proto 字段名 from 是 Python 关键字，只能 getattr。
    p = getattr(msg, "from")
    kind = p.WhichOneof("kind")
    return getattr(p, kind) if kind else "unknown"


def _first_text(msg: session_pb2.Message) -> str:
    for b in msg.content:
        if b.WhichOneof("block") == "text":
            return b.text
    return ""
