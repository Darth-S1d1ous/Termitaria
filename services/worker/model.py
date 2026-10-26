"""模型出口（架构 §4 LLM client 的替换点）。

ModelPort 是协议。StubModel 产出确定性假流，供离线测试与未配 key 时的链路验收。
具体提供商在 adapters 里装配（nvidia.NvidiaModel、Nebius、DeepSeek 官方）。
select_model 是唯一装配点。graph / runner 只依赖 stream()。
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
# deepseek-v4.1-flash：TTFB ~1 分钟；kimi-k3 在 Catalog 排队 2-3 分钟，先不用。
DEFAULT_MODEL = "deepseek-ai/deepseek-v4.1-flash"
DEFAULT_MAX_TOKENS = 16384


@dataclass(frozen=True)
class ModelConfig:
    """一个模型出口的运行参数。有 api_key 才实例化 NVIDIA。"""

    api_key: str = ""
    model: str = DEFAULT_MODEL
    temperature: float = 1.0
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: float = 300.0
    # 空字符串保持原装配：有 NVIDIA key 走 Catalog，否则 StubModel。
    provider: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1"


class ModelPort(Protocol):
    async def stream(
        self,
        *,
        system: str,
        window: list[tuple[str, str]],
        model: common_pb2.ModelRef,
        max_tokens: int,
        tools: list | None = None,
    ) -> AsyncIterator[ModelEvent]:
        """流式产出文本增量，最后一个事件必须是 Done(usage)。

        model.model 是 Go 侧解析好的模型名。占位名 stub-model 由具体提供商换成自己的默认模型。
        tools 是发给模型的函数声明。有原生调用时，具体模型在正文之外另行交出。
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
        tools: list | None = None,
    ) -> AsyncIterator[ModelEvent]:
        del tools
        last = window[-1][1] if window else ""
        text = (
            f"[stub:{model.model or 'unspecified'}] {len(window)} messages in the window"
            + (f", last: {last[:80]!r}" if last else "")
            + ". The LangGraph skeleton is up. The LLM client is not connected."
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
    """按 provider 装配。未指定时与原来一样：有 NVIDIA key 走 Catalog，否则 StubModel。

    MODEL_CLOUD_ENABLED=1 时返回 RoutingModel，按 task 的 ModelRef.provider 路由；
    默认仍是进程级 provider，所以 DeepSeek 测试不会碰到 Nebius/NVIDIA。
    """
    from .adapters import RoutingModel, build_model, cloud_enabled

    cfg = cfg or ModelConfig()
    if cloud_enabled():
        return RoutingModel(cfg)
    return build_model(cfg)


def _split_chunks(text: str, n: int) -> list[str]:
    if n <= 1 or len(text) <= n:
        return [text]
    step = max(1, len(text) // n)
    return [text[i : i + step] for i in range(0, len(text), step)]
