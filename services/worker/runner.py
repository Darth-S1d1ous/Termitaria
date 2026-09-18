"""单条 Task 的执行器：驱动 LangGraph，双通道回写（contracts §4）。

- 流式 TaskDelta → task.delta_subject（core NATS，不持久化）；
- 最终 TaskResult → tasks.<swarm>.result.<task>（core NATS，至少一次）；
- 单写者边界：result 里的 Message 不填 message_id / turn_index / created_at_ms，
  由 session actor 落流时分配（worker 永远不写 sessions.*）。
"""
from __future__ import annotations

from termitaria.session.v1 import session_pb2
from termitaria.task.v1 import task_pb2

from . import envelope
from .graph import build_graph
from .model import ModelPort


class TaskRunner:
    def __init__(self, nc, model: ModelPort, producer: str) -> None:
        self._nc = nc
        self._model = model
        self._producer = producer

    async def run(self, task: task_pb2.Task, trace_id: str) -> task_pb2.TaskResult:
        async def emit(delta: task_pb2.TaskDelta) -> None:
            # delta 的 subject 以 Task 字段为准，worker 不自己拼
            await self._nc.publish(
                task.delta_subject, envelope.pack(self._producer, trace_id, delta)
            )

        graph = build_graph(self._model, emit)
        state = await graph.ainvoke({"task": task, "seq": 0, "text": "", "cancelled": False})

        result = self._build_result(task, state)
        # result subject 在 Task 契约里没有字段，按 internal/bus/subjects.go 的分类法推导
        await self._nc.publish(
            f"tasks.{task.swarm_id}.result.{task.task_id}",
            envelope.pack(self._producer, trace_id, result),
        )
        return result

    @staticmethod
    def _build_result(task: task_pb2.Task, state: dict) -> task_pb2.TaskResult:
        err = state.get("error")
        if state.get("cancelled"):
            status = task_pb2.TASK_STATUS_CANCELLED
        elif err is not None and err.code:
            status = task_pb2.TASK_STATUS_FAILED
        else:
            status = task_pb2.TASK_STATUS_COMPLETED

        message = session_pb2.Message(
            session_id=task.session_id,
            reply_to_message_id=task.trigger_message_id,
            content=[session_pb2.ContentBlock(text=state.get("text", ""))],
        )
        getattr(message, "from").agent_id = task.agent_id  # from 是 Python 关键字

        result = task_pb2.TaskResult(task_id=task.task_id, status=status, message=message)
        usage = state.get("usage")
        if usage is not None:
            result.usage.CopyFrom(usage)
        if err is not None and err.code:
            result.error.CopyFrom(err)
        return result
