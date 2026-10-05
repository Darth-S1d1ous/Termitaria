"""模型出口（架构 §4 LLM client 的替换点）。

ModelPort 是协议。StubModel 产出确定性假流，供离线测试与未配 key 时的链路验收。
具体提供商在各自模块里实现（目前是 nvidia.NvidiaModel）；select_model 是唯一装配点。
graph / runner 只依赖 stream()。
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

# Catalog 示例的默认模型。控制面下发真实模型名时不用它们。
DEFAULT_MODEL = "moonshotai/kimi-k3"
DEFAULT_MAX_TOKENS = 16384


@dataclass(frozen=True)
class ModelConfig:
    """一个模型出口的运行参数。有 api_key 才实例化真实提供商。"""

    api_key: str = ""
    model: str = DEFAULT_MODEL
    temperature: float = 1.0
    max_tokens: int = DEFAULT_MAX_TOKENS


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

        model.model 是 Go 侧解析好的模型名。占位名 stub-model 由具体提供商换成自己的默认模型。
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


def select_model(cfg: ModelConfig | None = None):
    """有 key 时走 NVIDIA API Catalog，否则 StubModel。"""
    cfg = cfg or ModelConfig()
    if not cfg.api_key:
        return StubModel()
    from .nvidia import NvidiaModel

    return NvidiaModel(
        cfg.api_key,
        default_model=cfg.model,
        temperature=cfg.temperature,
        max_completion_tokens=cfg.max_tokens,
    )


def _split_chunks(text: str, n: int) -> list[str]:
    if n <= 1 or len(text) <= n:
        return [text]
    step = max(1, len(text) // n)
    return [text[i : i + step] for i in range(0, len(text), step)]
