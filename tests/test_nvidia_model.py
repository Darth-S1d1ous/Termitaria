"""NvidiaModel：不打网络，假 ChatNVIDIA 验证消息映射、推理增量与用量。"""
import asyncio

from services.worker.model import Chunk, Done, StubModel, select_model
from services.worker.nvidia import NvidiaModel
from termitaria.common.v1 import common_pb2


class _Chunk:
    def __init__(self, content="", reasoning="", usage=None):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
        self.usage_metadata = usage


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        yield _Chunk(reasoning="think", usage={"input_tokens": 3, "output_tokens": 1})
        yield _Chunk(content="answer", usage={"input_tokens": 3, "output_tokens": 4})


def _run(port, **kwargs):
    async def collect():
        out = []
        async for ev in port.stream(**kwargs):
            out.append(ev)
        return out

    return asyncio.run(collect())


def _ref(name: str) -> common_pb2.ModelRef:
    return common_pb2.ModelRef(provider="nvidia", model=name)


def test_placeholder_model_uses_nvidia_defaults():
    holder = {}

    def factory(**kwargs):
        client = _Client(**kwargs)
        holder["client"] = client
        return client

    model = NvidiaModel("test-key", client_factory=factory)
    events = _run(
        model,
        system="you are ideator",
        window=[("u_demo", "介绍一下你自己")],
        model=_ref("stub-model"),
        max_tokens=512,
    )

    client = holder["client"]
    assert client.kwargs == {
        "model": "moonshotai/kimi-k3",
        "api_key": "test-key",
        "temperature": 1.0,
        "max_completion_tokens": 16384,
    }
    assert client.messages == [
        {"role": "system", "content": "you are ideator"},
        {"role": "user", "content": "u_demo: 介绍一下你自己"},
    ]
    assert [ev.text for ev in events if isinstance(ev, Chunk)] == ["answer"]
    done = events[-1]
    assert isinstance(done, Done)
    assert done.usage.prompt_tokens == 3
    assert done.usage.completion_tokens == 4
    assert done.usage.model == "moonshotai/kimi-k3"


def test_explicit_model_keeps_task_budget():
    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return _Client(**kwargs)

    model = NvidiaModel("k", default_model="other/model", temperature=0.2, client_factory=factory)
    _run(
        model,
        system="",
        window=[("a", "hi")],
        model=_ref("moonshotai/kimi-k3"),
        max_tokens=1024,
    )
    assert seen["model"] == "moonshotai/kimi-k3"
    assert seen["max_completion_tokens"] == 1024
    assert seen["temperature"] == 0.2


def test_select_model_without_key_is_stub():
    assert isinstance(select_model(), StubModel)
