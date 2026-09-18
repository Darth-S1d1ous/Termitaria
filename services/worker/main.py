"""worker 入口。

用法：
    docker compose up -d nats
    WORKER_AGENTS=ideator,reviewer .venv/bin/python -m services.worker.main
"""
from __future__ import annotations

import asyncio
import logging

import nats

from .config import WorkerConfig
from .consumer import AgentConsumer
from .model import StubModel
from .runner import TaskRunner


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    cfg = WorkerConfig.from_env()
    nc = await nats.connect(cfg.nats_url, name="termitaria-worker")
    # LLM client（Token Factory）本切片不实现；替换点见 model.ModelPort
    model = StubModel()
    runner = TaskRunner(nc, model, cfg.producer)
    consumers = [AgentConsumer(nc, agent_id, runner, cfg) for agent_id in cfg.agent_ids]
    try:
        await asyncio.gather(*(c.run() for c in consumers))
    finally:
        await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
