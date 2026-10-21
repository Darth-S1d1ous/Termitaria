"""memory 服务运行配置，全部走环境变量（风格对齐 services/worker/config.py）。

MEMORY_BACKEND=canned（默认）| mem0 | graphiti。
后两个需要：uv sync --extra memory
  docker compose up -d qdrant      # mem0，QDRANT_HOST / QDRANT_PORT
  docker compose up -d falkordb    # graphiti，浏览器在宿主机 3001
抽取用的聊天模型：
  MEMORY_LLM_API_KEY，空则 NEBIUS_API_KEY，再空则 OPENAI_API_KEY
  MEMORY_LLM_BASE_URL，空则 NEBIUS_BASE_URL，再空则 https://api.openai.com/v1
  MEMORY_LLM_MODEL（要选 mem0 或 graphiti 时必须设）
嵌入默认 MEMORY_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2，本地算，不走 API。
key、模型名或对应存储连不上时，服务退回 canned，召回 meta.degraded=true。
"""
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
    backend: str = "canned"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "termitaria_mem0"
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    http_addr: str = "127.0.0.1:8791"

    @classmethod
    def from_env(cls) -> Self:
        backend = os.environ.get("MEMORY_BACKEND", "canned").strip().lower() or "canned"
        return cls(
            nats_url=os.environ.get("NATS_URL", "nats://localhost:4222"),
            spool_dir=os.environ.get("MEMORY_SPOOL_DIR", "./var/memory"),
            backend=backend,
            llm_api_key=(
                os.environ.get("MEMORY_LLM_API_KEY")
                or os.environ.get("NEBIUS_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or ""
            ),
            llm_base_url=(
                os.environ.get("MEMORY_LLM_BASE_URL")
                or os.environ.get("NEBIUS_BASE_URL")
                or "https://api.openai.com/v1"
            ),
            llm_model=os.environ.get("MEMORY_LLM_MODEL", "").strip(),
            embed_model=os.environ.get(
                "MEMORY_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            ).strip()
            or "sentence-transformers/all-MiniLM-L6-v2",
            qdrant_host=os.environ.get("QDRANT_HOST", "localhost"),
            qdrant_port=int(os.environ.get("QDRANT_PORT", "6333")),
            qdrant_collection=os.environ.get("QDRANT_COLLECTION", "termitaria_mem0"),
            falkordb_host=os.environ.get("FALKORDB_HOST", "localhost"),
            falkordb_port=int(os.environ.get("FALKORDB_PORT", "6379")),
            http_addr=os.environ.get("MEMORY_HTTP_ADDR", "127.0.0.1:8791").strip()
            or "127.0.0.1:8791",
        )