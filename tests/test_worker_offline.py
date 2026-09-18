"""离线验证：不依赖 NATS —— Envelope 往返 + StubModel 跑通整条 graph + runner 回写。

运行：.venv/bin/pytest
"""
import asyncio

from services.worker import envelope
from services.worker.model import StubModel
from services.worker.runner import TaskRunner
from termitaria.common.v1 import common_pb2
from termitaria.session.v1 import session_pb2
from termitaria.task.v1 import task_pb2


def make_task() -> task_pb2.Task:
    msg = session_pb2.Message(
        content=[session_pb2.ContentBlock(text="reviewer 怎么看这个假设？")]
    )
    getattr(msg, "from").user_id = "u1"  # from 是 Python 关键字
    return task_pb2.Task(
        task_id="t1",
        swarm_id="sw1",
        agent_id="ideator",
        session_id="s1",
        trigger_message_id="m_1",
        context=task_pb2.TaskContext(window=[msg]),
        budget=common_pb2.Budget(max_tokens=512),
        model=common_pb2.ModelRef(
            provider="nebius-token-factory",
            tier=common_pb2.MODEL_TIER_SUPER,
            model="stub-model",
        ),
        delta_subject="tasks.sw1.delta.t1",
    )


def test_envelope_roundtrip():
    data = envelope.pack("test", "trace-1", make_task())
    env, got = envelope.unpack(data, task_pb2.Task)
    assert env.trace_id == "trace-1"
    assert env.schema == "termitaria.task.v1.Task"
    assert env.event_id
    assert got.task_id == "t1"


def test_envelope_schema_mismatch():
    data = envelope.pack("test", "", make_task())
    try:
        envelope.unpack(data, session_pb2.SessionEvent)
    except envelope.SchemaMismatchError:
        return
    raise AssertionError("schema mismatch 应抛错")


class FakeNC:
    """只收集 publish 的假 NATS 连接。"""

    def __init__(self):
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject: str, data: bytes) -> None:
        self.published.append((subject, data))


def _deltas(nc: FakeNC) -> list[task_pb2.TaskDelta]:
    return [
        envelope.unpack(d, task_pb2.TaskDelta)[1]
        for s, d in nc.published
        if ".delta." in s
    ]


def test_runner_emits_deltas_then_result():
    nc = FakeNC()
    runner = TaskRunner(nc, StubModel(chunk_delay_s=0), producer="test")
    result = asyncio.run(runner.run(make_task(), "trace-1"))

    deltas = _deltas(nc)
    assert len(deltas) >= 2  # 至少一条 text + 收尾 usage
    seqs = [d.seq for d in deltas]
    assert seqs == list(range(1, len(seqs) + 1))  # seq 从 1 单调递增
    assert all(d.task_id == "t1" for d in deltas)
    assert deltas[-1].WhichOneof("delta") == "usage"  # 收尾帧是 usage

    results = [(s, d) for s, d in nc.published if s == "tasks.sw1.result.t1"]
    assert len(results) == 1
    _, payload = envelope.unpack(results[0][1], task_pb2.TaskResult)
    assert payload.status == task_pb2.TASK_STATUS_COMPLETED
    # 单写者边界：message_id / turn_index / created_at_ms 由 actor 分配
    assert payload.message.message_id == ""
    assert payload.message.turn_index == 0
    assert payload.message.created_at_ms == 0
    assert getattr(payload.message, "from").agent_id == "ideator"
    assert payload.message.reply_to_message_id == "m_1"
    assert payload.message.content[0].text  # stub 拼出了非空文本
    assert payload.usage.model == "stub-model"
    assert result.status == task_pb2.TASK_STATUS_COMPLETED


def test_budget_deadline_cancels():
    task = make_task()
    task.budget.deadline_ms = 1  # 1970-01-01，必然过期（proto 语义：绝对 epoch ms）
    nc = FakeNC()
    runner = TaskRunner(nc, StubModel(chunk_delay_s=0), producer="test")
    result = asyncio.run(runner.run(task, "trace-1"))

    assert result.status == task_pb2.TASK_STATUS_CANCELLED
    assert result.error.code == "BUDGET_EXCEEDED"
    deltas = _deltas(nc)
    assert deltas[-1].WhichOneof("delta") == "error"  # 收尾帧是 error


def test_model_error_fails_task():
    class BoomModel:
        async def stream(self, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover - 让它成为 async generator

    nc = FakeNC()
    runner = TaskRunner(nc, BoomModel(), producer="test")
    result = asyncio.run(runner.run(make_task(), "trace-1"))

    assert result.status == task_pb2.TASK_STATUS_FAILED
    assert result.error.code == "WORKER_ERROR"
    assert result.error.retryable is False
