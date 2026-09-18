"""模型出口（架构 §4 LLM client 的替换点）。

本切片不实现 LLM client：ModelPort 是协议，StubModel 产出确定性的假流式响应，
用于 M1 链路验收（Go Task → worker → delta/result → MessageAppended）。
接 Token Factory 时实现同一个 stream() 签名即可，graph / runner 不用动。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Protocol, Union

from termitaria.common.v1 import common_pb2
from termitaria.task.v1 import task_pb2


@dataclass
class Chunk:
    """一段文本增量。"""

    text: str


@dataclass
class Done:
    """流结束，携带 token 用量（demo 展示模型分层与成本的数据源，架构 §7）。"""

    usage: task_pb2.TaskUsage


ModelEvent = Union[Chunk, Done]


class ModelPort(Protocol):
    async def stream(
        self,
        *,
        system: str,
        window: list[tuple[str, str]],
        model: common_pb2.ModelRef,
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        """流式产出文本增量，最后一个事件必须是 Done(usage)。

        真实实现：OpenAI 兼容 client → Nebius Token Factory。
        model.model 是唯一权威的模型名（model router 已在 Go 侧解析好档位）。
        """
        ...


class StubModel:
    """确定性假模型：把窗口概况拼成一段话，切成若干 chunk 假流式输出。"""

    def __init__(self, chunk_delay_s: float = 0.02) -> None:
        self._delay = chunk_delay_s

    async def stream(
        self,
        *,
        system: str,
        window: list[tuple[str, str]],
        model: common_pb2.ModelRef,
        max_tokens: int,
    ) -> AsyncIterator[ModelEvent]:
        last = window[-1][1] if window else ""
        text = (
            f"[stub:{model.model or 'unspecified'}] 窗口 {len(window)} 条消息"
            + (f"，最后一条：{last[:80]!r}" if last else "")
            + "。LangGraph 骨架已通，等 LLM client 接入。"
        )
        prompt_tokens = len(system.split()) + sum(len(t.split()) for _, t in window)
        completion_tokens = 0
        for piece in _split_chunks(text, 6):
            if self._delay:
                await asyncio.sleep(self._delay)
            completion_tokens += max(1, len(piece.split()))
            yield Chunk(piece)
        yield Done(
            task_pb2.TaskUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,
                model=model.model or "stub",
            )
        )


def _split_chunks(text: str, n: int) -> list[str]:
    if n <= 1 or len(text) <= n:
        return [text]
    step = max(1, len(text) // n)
    return [text[i : i + step] for i in range(0, len(text), step)]
