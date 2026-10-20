"""OpenAI Chat Completions 流式客户端。

Nebius Token Factory 和 DeepSeek 官方都吃这套请求。NVIDIA Catalog 不走这里。
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from termitaria.common.v1 import common_pb2
from termitaria.task.v1 import task_pb2

from .calls import Call, extract_tool_call
from .model import DEFAULT_MAX_TOKENS, Chunk, Done

Transport = Callable[[str, dict], list[dict]]


class OpenAIChatModel:
    def __init__(
        self,
        api_key: str,
        *,
        provider: str,
        base_url: str,
        default_model: str,
        temperature: float = 1.0,
        max_completion_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = 300.0,
        extra: dict | None = None,
        map_model: Callable[[str], str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.provider = provider
        self.default_model = default_model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._max_completion_tokens = max_completion_tokens or DEFAULT_MAX_TOKENS
        self._timeout = timeout
        self._extra = extra or {}
        self._map_model = map_model
        self._transport = transport or _post_sse

    async def stream(
        self,
        *,
        system: str,
        window: list[tuple[str, str]],
        model: common_pb2.ModelRef,
        max_tokens: int,
        tools: list | None = None,
    ):
        name, limit = self._resolve(model, max_tokens)
        body = self._body(name, system, window, limit, tools)
        # HTTP 放在线程里，事件循环才能继续发 in_progress，避免任务被重新投递。
        payloads = await asyncio.to_thread(
            self._transport,
            _chat_url(self._base_url),
            {**body, "_api_key": self._api_key, "_timeout": self._timeout},
        )
        text, calls, prompt_tokens, completion_tokens = _read_payloads(payloads)
        if not text and not calls:
            raise RuntimeError("model returned no content")
        if text:
            yield Chunk(text)
        for call in calls:
            yield call
        yield Done(
            task_pb2.TaskUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,
                model=name,
            )
        )

    def _resolve(self, model: common_pb2.ModelRef, max_tokens: int) -> tuple[str, int]:
        name = model.model or ""
        if self._map_model is not None:
            name = self._map_model(name)
        if not name:
            name = self.default_model
        limit = max_tokens or self._max_completion_tokens
        if limit < 1:
            limit = self._max_completion_tokens
        return name, limit

    def _body(self, name: str, system: str, window, limit: int, tools: list | None) -> dict:
        body: dict = {
            "model": name,
            "messages": _messages(system, window),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": limit,
            "temperature": self._temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        body.update(self._extra)
        return body


def _messages(system: str, window: list[tuple[str, str]]) -> list[dict]:
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    for sender, text in window:
        body = f"{sender}: {text}" if sender else text
        msgs.append({"role": "user", "content": body})
    return msgs


def _chat_url(base: str) -> str:
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _read_payloads(payloads: list[dict]) -> tuple[str, list[Call], int, int]:
    parts: list[str] = []
    pending: dict = {}
    prompt_tokens = 0
    completion_tokens = 0
    for payload in payloads:
        usage = payload.get("usage") or {}
        if usage.get("prompt_tokens") or usage.get("completion_tokens"):
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            # reasoning_content 是思考过程，不是给用户的话。
            content = delta.get("content") or ""
            if content:
                parts.append(content)
            for raw in delta.get("tool_calls") or []:
                _accumulate(pending, raw)
    calls: list[Call] = []
    for index in sorted(pending):
        call = extract_tool_call(pending[index])
        if call is not None:
            calls.append(call)
    return "".join(parts), calls, prompt_tokens, completion_tokens


def _accumulate(pending: dict, raw: dict) -> None:
    index = raw.get("index")
    if index is None:
        index = raw.get("id") or len(pending)
    slot = pending.setdefault(index, {"function": {"name": "", "arguments": ""}})
    function = raw.get("function") or {}
    if function.get("name"):
        slot["function"]["name"] = function["name"]
    piece = function.get("arguments")
    if isinstance(piece, str):
        slot["function"]["arguments"] += piece
    elif isinstance(piece, dict):
        slot["function"]["arguments"] = json.dumps(piece)


def _post_sse(url: str, packed: dict) -> list[dict]:
    api_key = packed.pop("_api_key")
    timeout = packed.pop("_timeout")
    data = json.dumps(packed).encode()
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as res:
            raw = res.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"模型接口 HTTP {exc.code}：{detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"模型接口连不上：{exc}") from exc
    return list(_sse_payloads(raw.decode("utf-8", "replace")))


def _sse_payloads(text: str) -> Iterator[dict]:
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        if data:
            yield json.loads(data)
