"""worker 入口。

用法：
    docker compose up -d nats
    .venv/bin/python -m services.worker.main

不设 WORKER_AGENTS 时消费全部 agent 队列。角色来自 SwarmSpec，不来自这条命令。
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
    if cfg.agent_ids:
        consumers = [AgentConsumer(nc, agent_id, runner, cfg) for agent_id in cfg.agent_ids]
    else:
        logging.getLogger(__name__).info("consuming tasks.*.* (all agents)")
        consumers = [AgentConsumer(nc, None, runner, cfg)]
    try:
        await asyncio.gather(*(c.run() for c in consumers))
    finally:
        await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
