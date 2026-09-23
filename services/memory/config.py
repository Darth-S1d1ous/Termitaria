"""memory 服务运行配置，全部走环境变量（风格对齐 services/worker/config.py）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Self

@dataclass(frozen=True)
class MemoryConfig:
    nats_url: str
    spool_dir: str = "./var/memory"      # write 路径的 JSONL 落盘目录
    producer: str = "memory-service"     # Envelope.producer
    fetch_timeout_s: float = 1.0
    fetch_batch: int = 8

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            nats_url=os.environ.get("NATS_URL", "nats://localhost:4222"),
            spool_dir=os.environ.get("MEMORY_SPOOL_DIR", "./var/memory"),
        )