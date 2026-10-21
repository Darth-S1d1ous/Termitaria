"""memory 服务入口。
用法：
    docker compose up -d nats
    .venv/bin/python -m services.memory.main
"""

from __future__ import annotations
import asyncio
import logging
import nats
from termitaria.memory.v1 import memory_pb2
from .backend import build_backend
from .config import MemoryConfig
from .distiller import Distiller
from .server import RecallResponder, WriteConsumer
from .timing import publish_recall_wait

async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    cfg = MemoryConfig.from_env()
    backend = await build_backend(cfg)
    try:
        await publish_recall_wait(backend)
    except Exception:
        logging.getLogger(__name__).exception("recall warmup failed")
    nc = await nats.connect(cfg.nats_url, name="termitaria-memory")

    responder = RecallResponder(nc, cfg, backend)
    episode_writer = WriteConsumer(
        nc, cfg,
        subject="memory.write.episode",
        durable="memory-write-episode",
        payload_type=memory_pb2.WriteEpisodeRequest,
        spool_file="episodes.jsonl",
        backend=backend,
    )
    document_writer = WriteConsumer(
        nc, cfg,
        subject="memory.write.document",
        durable="memory-write-document",
        payload_type=memory_pb2.WriteDocumentRequest,
        spool_file="documents.jsonl",
        backend=backend,
    )
    distiller = Distiller(nc, cfg)

    try:
        await asyncio.gather(
            responder.run(),
            episode_writer.run(),
            document_writer.run(),
            distiller.run(),
        )
    finally:
        await nc.drain()

if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass