"""NvidiaModel：不打网络，假 ChatNVIDIA 验证消息映射、推理增量与用量。"""
import asyncio

from services.worker.model import Chunk, Done, StubModel, select_model
from services.worker.nvidia import NvidiaModel
from termitaria.common.v1 import common_pb2


class _Chunk:
    def __init__(self, content="", reasoning="", usage=None, tool_call_chunks=None):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
        self.usage_metadata = usage
        self.tool_call_chunks = tool_call_chunks or []


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = None

    def bind_tools(self, tools, **kwargs):
        self.bound = tools
        return self

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
        "model": "deepseek-ai/deepseek-v4.1-flash",
        "api_key": "test-key",
        "temperature": 1.0,
        "max_completion_tokens": 16384,
        "timeout": 300.0,
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
    assert done.usage.model == "deepseek-ai/deepseek-v4.1-flash"


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
        model=_ref("deepseek-ai/deepseek-v4.1-flash"),
        max_tokens=1024,
    )
    assert seen["model"] == "deepseek-ai/deepseek-v4.1-flash"
    assert seen["max_completion_tokens"] == 16384
    assert seen["temperature"] == 0.2
    _run(
        model,
        system="",
        window=[("a", "hi")],
        model=_ref("deepseek-ai/deepseek-v4.1-flash"),
        max_tokens=20000,
    )
    assert seen["max_completion_tokens"] == 20000


def test_native_tool_chunks_become_calls():
    from services.worker.calls import Call

    class Calling(_Client):
        async def astream(self, messages):
            self.messages = messages
            yield _Chunk(
                content="",
                tool_call_chunks=[{
                    "index": 0,
                    "id": "call_9",
                    "name": "web_search",
                    "args": "{\"query\":\"tsla\"}",
                }],
                usage={"input_tokens": 2, "output_tokens": 3},
            )

    model = NvidiaModel("k", client_factory=lambda **kwargs: Calling(**kwargs))
    events = _run(
        model,
        system="s",
        window=[("u", "查")],
        model=_ref("deepseek-ai/deepseek-v4.1-flash"),
        max_tokens=20000,
        tools=[{"type": "function", "function": {"name": "web_search"}}],
    )
    calls = [ev for ev in events if isinstance(ev, Call)]
    assert calls[0].name == "web_search"
    assert calls[0].arguments == {"query": "tsla"}


def test_select_model_without_key_is_stub():
    assert isinstance(select_model(), StubModel)
