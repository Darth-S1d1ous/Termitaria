"""worker 运行配置，全部走环境变量（十二要素；与 .env.example 对齐）。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .model import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, ModelConfig


@dataclass(frozen=True)
class WorkerConfig:
    nats_url: str
    agent_ids: tuple[str, ...]   # 本进程服务的 agent；每个 agent 一个 durable consumer
    model: ModelConfig
    producer: str = "langgraph-worker"  # Envelope.producer
    concurrency: int = 4         # 单 agent 并发处理的 task 上限
    fetch_timeout_s: float = 1.0

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        agents = tuple(
            a.strip() for a in os.environ.get("WORKER_AGENTS", "").split(",") if a.strip()
        )
        if not agents:
            raise SystemExit(
                "WORKER_AGENTS 为空：逗号分隔的 agent id 列表，例：WORKER_AGENTS=ideator,reviewer"
            )
        return cls(
            nats_url=os.environ.get("NATS_URL", "nats://localhost:4222"),
            agent_ids=agents,
            concurrency=int(os.environ.get("WORKER_CONCURRENCY", "4")),
            model=ModelConfig(
                api_key=os.environ.get("NVIDIA_API_KEY", ""),
                model=os.environ.get("NVIDIA_MODEL", DEFAULT_MODEL),
                temperature=float(os.environ.get("NVIDIA_TEMPERATURE", "1")),
                max_tokens=int(os.environ.get("NVIDIA_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
            ),
        )
