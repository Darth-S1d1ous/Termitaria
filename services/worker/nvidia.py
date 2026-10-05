"""NVIDIA API Catalog 模型出口。

用 langchain_nvidia_ai_endpoints.ChatNVIDIA 调 integrate.api.nvidia.com，
不经过 Nebius。默认模型与参数对齐 Catalog 的 LangChain 示例（moonshotai/kimi-k3）。
Go 侧还没配模型时 Task 上是占位名 stub-model，这里换成 NVIDIA_MODEL。
"""
from __future__ import annotations

from typing import AsyncIterator, Callable

from termitaria.common.v1 import common_pb2
from termitaria.task.v1 import task_pb2

from .model import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, Chunk, Done, ModelEvent

_PLACEHOLDER_MODELS = {"", "stub-model"}


class NvidiaModel:
    def __init__(
        self,
        api_key: str,
        *,
        default_model: str = DEFAULT_MODEL,
        temperature: float = 1.0,
        max_completion_tokens: int = DEFAULT_MAX_TOKENS,
        client_factory: Callable | None = None,
    ) -> None:
        self._api_key = api_key
        self._default_model = default_model or DEFAULT_MODEL
        self._temperature = temperature
        self._max_completion_tokens = max_completion_tokens or DEFAULT_MAX_TOKENS
        self._client_factory = client_factory or _chat_nvidia
        # ChatNVIDIA 初始化会拉一次模型列表，按 (模型, 长度, 温度) 复用客户端。
        self._clients: dict[tuple[str, int, float], object] = {}

    async def stream(
        self,
        *,
        system: str,
        window: list[tuple[str, str]],
        model: common_pb2.ModelRef,
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        name, limit = self._resolve(model, max_tokens)
        client = self._client(name, limit)
        prompt_tokens = 0
        completion_tokens = 0
        saw_content = False
        async for chunk in client.astream(_messages(system, window)):
            got_in, got_out = _read_usage(chunk)
            if got_in or got_out:
                prompt_tokens, completion_tokens = got_in, got_out
            # 推理过程不是发言。kimi 一类模型会先吐 reasoning_content，
            # 预算不够时推理被截成「!!!!」且永远没有正文。
            text = _chunk_text(chunk)
            if text:
                saw_content = True
                yield Chunk(text)
        if not saw_content:
            raise RuntimeError(
                "model returned no content; reasoning was truncated or empty"
            )
        yield Done(
            task_pb2.TaskUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,
                model=name,
            )
        )

    def _resolve(self, model: common_pb2.ModelRef, max_tokens: int) -> tuple[str, int]:
        # 控制面 MVP 仍下发 stub-model / 512 token；接到 NVIDIA 时换成可用的模型与长度。
        name = model.model or ""
        if name in _PLACEHOLDER_MODELS:
            return self._default_model, self._max_completion_tokens
        return name, max_tokens or self._max_completion_tokens

    def _client(self, name: str, limit: int):
        key = (name, limit, self._temperature)
        client = self._clients.get(key)
        if client is None:
            client = self._client_factory(
                model=name,
                api_key=self._api_key,
                temperature=self._temperature,
                max_completion_tokens=limit,
            )
            self._clients[key] = client
        return client


def _chat_nvidia(**kwargs):
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    return ChatNVIDIA(**kwargs)


def _messages(system: str, window: list[tuple[str, str]]) -> list[dict]:
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    for sender, text in window:
        body = f"{sender}: {text}" if sender else text
        msgs.append({"role": "user", "content": body})
    return msgs


def _chunk_text(chunk) -> str:
    content = getattr(chunk, "content", "") or ""
    if not isinstance(content, str):
        return ""
    return content


def _read_usage(chunk) -> tuple[int, int]:
    usage = getattr(chunk, "usage_metadata", None)
    if not usage:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )
