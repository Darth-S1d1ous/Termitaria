"""graph 服务运行配置，全部走环境变量（风格对齐 services/worker/config.py）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True)
class GraphConfig:
    nats_url: str
    grpc_addr: str = "localhost:50051"   # KnowledgeGraphService 监听地址
    falkordb_url: str = "redis://localhost:6379"  # 同事换内核时用；stub 不连接
    spool_dir: str = "./var/graph"       # ingest 观测 JSONL
    producer: str = "graph-service"      # Envelope.producer（ingest 侧读 trace 用）
    load_canned: bool = True             # stub 启动时装罐装图；内核落地后关掉
    fetch_timeout_s: float = 1.0
    fetch_batch: int = 8
    query_timeout_s: float = 0.2         # client 降级阈值，对齐召回预算 50–200ms

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            nats_url=os.environ.get("NATS_URL", "nats://localhost:4222"),
            grpc_addr=os.environ.get("GRAPH_GRPC_ADDR", "localhost:50051"),
            falkordb_url=os.environ.get("FALKORDB_URL", "redis://localhost:6379"),
            spool_dir=os.environ.get("GRAPH_SPOOL_DIR", "./var/graph"),
            load_canned=os.environ.get("GRAPH_CANNED", "1") != "0",
            query_timeout_s=float(os.environ.get("GRAPH_QUERY_TIMEOUT_S", "0.2")),
        )
