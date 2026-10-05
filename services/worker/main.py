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
from .model import select_model
from .runner import TaskRunner


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    cfg = WorkerConfig.from_env()
    nc = await nats.connect(cfg.nats_url, name="termitaria-worker")
    model = select_model(cfg.model)
    logging.getLogger(__name__).info(
        "model source: %s",
        cfg.model.model if cfg.model.api_key else "stub",
    )
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
