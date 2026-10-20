"""worker 运行配置，全部走环境变量（十二要素；与 .env.example 对齐）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
        # 空名单 = 消费全部 tasks.*.*。角色在创建 swarm / 项目时确定，不在启动命令里写死。
        agents = tuple(
            a.strip() for a in os.environ.get("WORKER_AGENTS", "").split(",") if a.strip()
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
                timeout=float(os.environ.get("NVIDIA_TIMEOUT", "300")),
                provider=os.environ.get("MODEL_PROVIDER", ""),
                deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", "") or _secret(
                    "deepseek-secrets", "TERMITARIA_API"
                ),
                deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
                deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                nebius_api_key=os.environ.get("NEBIUS_API_KEY", ""),
                nebius_base_url=os.environ.get(
                    "NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1"
                ),
            ),
        )


def _secret(filename: str, key: str) -> str:
    """从 ~/.secrets 读一行 KEY=value。文件不存在就当没配。"""
    path = Path.home() / ".secrets" / filename
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    prefix = key + "="
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip('"').strip("'")
    return ""
