"""graph 服务入口。
用法：
    docker compose up -d nats
    .venv/bin/python -m services.graph.main
"""
from __future__ import annotations
import asyncio
import logging

import nats

from .canned import install
from .config import GraphConfig
from .ingest import Ingestor
from .server import serve
from .store import InMemoryGraph


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    cfg = GraphConfig.from_env()
    store = InMemoryGraph()
    if cfg.load_canned:
        await install(store)

    nc = await nats.connect(cfg.nats_url, name="termitaria-graph")
    server = await serve(cfg.grpc_addr, store)
    ingestor = Ingestor(nc, cfg, store)
    try:
        await asyncio.gather(server.wait_for_termination(), ingestor.run())
    finally:
        await server.stop(grace=2)
        await nc.drain()

if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass