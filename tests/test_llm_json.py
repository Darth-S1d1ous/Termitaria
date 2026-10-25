"""模型吐出的 JSON。截断的不修，收尾完整但语法坏了的才修。"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("json_repair")

from services.memory.graphiti_backend import load_llm_json  # noqa: E402


def test_valid_json_passes_through():
    assert load_llm_json('{"name": "Lewis"}', "stop") == {"name": "Lewis"}


def test_extra_data_is_repaired_when_the_model_finished():
    value = load_llm_json('{"name": "Lewis"}{"name": "Clark"}', "stop")
    assert value["name"] == "Lewis"


def test_unclosed_string_is_repaired_when_the_model_finished():
    value = load_llm_json('{"name": "Lew', "stop")
    assert value["name"] == "Lew"


def test_truncated_json_is_not_repaired():
    with pytest.raises(json.JSONDecodeError):
        load_llm_json('{"name": "Lew', "length")


def test_missing_finish_reason_is_not_repaired():
    with pytest.raises(json.JSONDecodeError):
        load_llm_json('{"name": "Lew', None)


def test_repairing_client_uses_the_library_model_constant():
    import asyncio

    pytest.importorskip("graphiti_core")
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import DEFAULT_MODEL
    from graphiti_core.prompts.models import Message

    from services.memory.graphiti_backend import _llm_client

    client = _llm_client(
        LLMConfig(api_key="test", model="deepseek-flash", base_url="https://api.deepseek.com")
    )
    seen: dict = {}

    class _Message:
        content = '{"name": "Lewis"}'

    class _Choice:
        message = _Message()
        finish_reason = "stop"

    class _Response:
        choices = [_Choice()]

    async def create(**kwargs):
        seen.update(kwargs)
        return _Response()

    async def go():
        client.client.chat.completions.create = create
        parsed = await client._generate_response([Message(role="user", content="extract")])
        assert parsed == {"name": "Lewis"}
        assert seen["model"] == "deepseek-flash"
        client.model = ""
        await client._generate_response([Message(role="user", content="extract")], model=None)
        assert seen["model"] == DEFAULT_MODEL

    asyncio.run(go())
