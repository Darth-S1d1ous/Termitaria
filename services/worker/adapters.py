"""模型提供商 adapter。

未指定 provider 时保持原行为：有 NVIDIA key 用 NvidiaModel，否则 StubModel。
nebius-token-factory 仍读 NEBIUS_API_KEY / NEBIUS_BASE_URL。没 key 时仍是 StubModel。
deepseek 走官方 API，不替换上面两家。
"""
from __future__ import annotations

import logging
import os

from .model import DEFAULT_MAX_TOKENS, ModelConfig, ModelPort, StubModel
from .openai_chat import OpenAIChatModel

_log = logging.getLogger(__name__)


def cloud_enabled() -> bool:
    """Cloud providers stay dormant until explicitly enabled; tests must not spend credit."""
    return os.environ.get("MODEL_CLOUD_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


class NvidiaAdapter:
    provider = "nvidia"

    def create(self, cfg: ModelConfig):
        from .nvidia import NvidiaModel

        port = NvidiaModel(
            cfg.api_key,
            default_model=cfg.model,
            temperature=cfg.temperature,
            max_completion_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
        )
        port.provider = self.provider
        port.default_model = cfg.model
        return port


class NebiusAdapter:
    provider = "nebius-token-factory"

    def create(self, cfg: ModelConfig):
        if not cfg.nebius_api_key:
            return StubModel()
        default = os.environ.get("MODEL_TIER_SUPER") or cfg.model
        return OpenAIChatModel(
            cfg.nebius_api_key,
            provider=self.provider,
            base_url=cfg.nebius_base_url,
            default_model=default,
            temperature=cfg.temperature,
            max_completion_tokens=cfg.max_tokens,
            timeout=cfg.timeout,
            map_model=lambda name: "" if name in {"", "stub-model"} else name,
        )


class DeepseekAdapter:
    provider = "deepseek"

    def create(self, cfg: ModelConfig):
        if not cfg.deepseek_api_key:
            _log.warning("MODEL_PROVIDER=deepseek 但没有 key，退回 StubModel")
            return StubModel()
        default = cfg.deepseek_model or "deepseek-flash"
        return OpenAIChatModel(
            cfg.deepseek_api_key,
            provider=self.provider,
            base_url=cfg.deepseek_base_url,
            default_model=default,
            temperature=cfg.temperature,
            max_completion_tokens=cfg.max_tokens or DEFAULT_MAX_TOKENS,
            timeout=cfg.timeout,
            # 官方默认开思考。思考模式下后续请求必须回传 reasoning_content，这里的窗口没有这个字段。
            # 关掉思考，函数调用走标准 tool_calls，也少掉每轮的长推理。
            extra={"thinking": {"type": "disabled"}},
            map_model=lambda name: _deepseek_name(name, default),
        )


_ADAPTERS = {
    "nvidia": NvidiaAdapter(),
    "nebius": NebiusAdapter(),
    "nebius-token-factory": NebiusAdapter(),
    "deepseek": DeepseekAdapter(),
}


def build_model(cfg: ModelConfig):
    provider = (cfg.provider or "").strip().lower()
    if provider in {"", "nvidia"}:
        if not cfg.api_key:
            return StubModel()
        return _ADAPTERS["nvidia"].create(cfg)
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        raise ValueError(f"unknown model provider {cfg.provider}")
    return adapter.create(cfg)


def _deepseek_name(name: str, default: str) -> str:
    """Catalog 上的 deepseek-ai/... 和占位名换成官方模型名。官方名原样用。"""
    if not name or name == "stub-model" or "/" in name:
        return default
    return name


class RoutingModel:
    """Pick a provider per task from ModelRef.provider.

    The process default stays exactly as before. A different provider is built
    lazily, so a DeepSeek-only test never touches Nebius or NVIDIA. Cloud
    providers refuse to start unless MODEL_CLOUD_ENABLED is on.
    """

    def __init__(self, cfg: ModelConfig) -> None:
        self._cfg = cfg
        self._ports: dict[str, ModelPort] = {}
        self.provider = (cfg.provider or "router").strip().lower() or "router"
        self.default_model = cfg.model
        self._default_provider = (cfg.provider or "").strip().lower() or "nvidia"

    def _build(self, provider: str) -> ModelPort:
        provider = (provider or "").strip().lower() or self._default_provider
        if provider == self._default_provider:
            return build_model(self._cfg)
        if provider == "deepseek":
            return DeepseekAdapter().create(self._cfg)
        if provider in {"nebius", "nebius-token-factory"}:
            if not cloud_enabled():
                raise RuntimeError("Nebius models are disabled; set MODEL_CLOUD_ENABLED=1")
            return NebiusAdapter().create(self._cfg)
        if provider == "nvidia":
            if not cloud_enabled():
                raise RuntimeError("NVIDIA models are disabled; set MODEL_CLOUD_ENABLED=1")
            return NvidiaAdapter().create(self._cfg)
        raise ValueError(f"unknown model provider {provider}")

    def _port(self, provider: str) -> ModelPort:
        key = (provider or "").strip().lower() or self._default_provider
        if key not in self._ports:
            self._ports[key] = self._build(key)
        return self._ports[key]

    async def stream(self, *, system: str, window, model, max_tokens: int, tools: list | None = None):
        port = self._port(model.provider)
        async for event in port.stream(
            system=system,
            window=window,
            model=model,
            max_tokens=max_tokens,
            tools=tools,
        ):
            yield event
