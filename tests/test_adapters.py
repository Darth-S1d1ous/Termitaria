"""提供商装配和 OpenAI 兼容流的解析。不打网络。"""
import asyncio

from services.worker.adapters import DeepseekAdapter, build_model
from services.worker.calls import Call
from services.worker.model import Chunk, Done, ModelConfig, StubModel
from services.worker.nvidia import NvidiaModel
from services.worker.openai_chat import OpenAIChatModel, _read_payloads
from termitaria.common.v1 import common_pb2


def _run(port, **kwargs):
    async def collect():
        out = []
        async for ev in port.stream(**kwargs):
            out.append(ev)
        return out

    return asyncio.run(collect())


def _ref(name: str) -> common_pb2.ModelRef:
    return common_pb2.ModelRef(provider="deepseek", model=name)


def test_unset_provider_with_nvidia_key_stays_nvidia():
    port = build_model(ModelConfig(api_key="nv-key", model="moonshotai/kimi-k3"))
    assert isinstance(port, NvidiaModel)
    assert port.provider == "nvidia"


def test_unset_provider_without_key_stays_stub():
    assert isinstance(build_model(ModelConfig()), StubModel)


def test_nebius_without_key_stays_stub():
    port = build_model(ModelConfig(provider="nebius-token-factory", api_key="nv-key"))
    assert isinstance(port, StubModel)


def test_deepseek_maps_catalog_name_and_parses_tool_calls():
    seen = {}

    def transport(url, packed):
        seen["url"] = url
        seen["body"] = {k: v for k, v in packed.items() if not k.startswith("_")}
        return [
            {
                "choices": [{
                    "delta": {
                        "reasoning_content": "先查一下",
                        "tool_calls": [{
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "web_search", "arguments": "{\"query\":"},
                        }],
                    }
                }]
            },
            {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "function": {"arguments": "\"tsla close\"}"},
                        }],
                    }
                }],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        ]

    cfg = ModelConfig(
        provider="deepseek",
        api_key="nv-key",
        deepseek_api_key="sk-test",
        deepseek_model="deepseek-flash",
    )
    port = DeepseekAdapter().create(cfg)
    assert isinstance(port, OpenAIChatModel)
    port._transport = transport
    events = _run(
        port,
        system="你是 quotes",
        window=[("lead", "查上周收盘价")],
        model=_ref("deepseek-ai/deepseek-v4.1-flash"),
        max_tokens=1024,
        tools=[{"type": "function", "function": {"name": "web_search"}}],
    )
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["body"]["model"] == "deepseek-flash"
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert seen["body"]["tools"][0]["function"]["name"] == "web_search"
    assert seen["body"]["messages"][0]["role"] == "system"
    assert seen["body"]["messages"][1] == {"role": "user", "content": "lead: 查上周收盘价"}
    calls = [ev for ev in events if isinstance(ev, Call)]
    texts = [ev.text for ev in events if isinstance(ev, Chunk)]
    assert texts == []
    assert calls[0].name == "web_search"
    assert calls[0].arguments == {"query": "tsla close"}
    done = events[-1]
    assert isinstance(done, Done)
    assert done.usage.model == "deepseek-flash"
    assert done.usage.prompt_tokens == 11


def test_deepseek_uses_the_task_model_name():
    seen = {}

    def transport(url, packed):
        seen["body"] = {k: v for k, v in packed.items() if not k.startswith("_")}
        return [
            {"choices": [{"delta": {"content": "ok"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        ]

    cfg = ModelConfig(provider="deepseek", deepseek_api_key="sk-test", deepseek_model="deepseek-flash")
    port = DeepseekAdapter().create(cfg)
    port._transport = transport
    events = _run(
        port,
        system="s",
        window=[("u", "hi")],
        model=_ref("deepseek-v4-pro"),
        max_tokens=512,
    )
    assert seen["body"]["model"] == "deepseek-v4-pro"
    assert events[-1].usage.model == "deepseek-v4-pro"


def test_router_keeps_cloud_providers_dormant(monkeypatch):
    monkeypatch.delenv("MODEL_CLOUD_ENABLED", raising=False)
    from services.worker.adapters import RoutingModel

    router = RoutingModel(ModelConfig(provider="deepseek", deepseek_api_key="sk-test", nebius_api_key="nb"))
    try:
        router._port("nebius-token-factory")
    except RuntimeError as exc:
        assert "MODEL_CLOUD_ENABLED" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_router_builds_the_selected_provider(monkeypatch):
    monkeypatch.setenv("MODEL_CLOUD_ENABLED", "1")
    from services.worker.adapters import RoutingModel

    router = RoutingModel(
        ModelConfig(
            provider="deepseek",
            deepseek_api_key="sk-test",
            nebius_api_key="nb",
            nebius_base_url="https://nebius.test/v1",
        )
    )
    assert router._port("deepseek").provider == "deepseek"
    assert router._port("nebius-token-factory").provider == "nebius-token-factory"


def test_reasoning_content_is_not_the_answer():
    text, calls, _, _ = _read_payloads([
        {"choices": [{"delta": {"reasoning_content": '{"tool":"web_search"}', "content": "平均收盘价 367.52"}}]},
    ])
    assert calls == []
    assert text == "平均收盘价 367.52"


def test_unknown_provider_is_rejected():
    try:
        build_model(ModelConfig(provider="other", api_key="k"))
    except ValueError as exc:
        assert "other" in str(exc)
    else:
        raise AssertionError("expected ValueError")
