"""记忆后端选择。canned 不依赖外部存储；mem0 / graphiti 起不来就退回 canned。"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from termitaria.memory.v1 import memory_pb2

from .canned import build_recall_response
from .config import MemoryConfig

log = logging.getLogger(__name__)


class CannedBackend:
    name = "canned"

    def __init__(self, *, degraded: bool = False) -> None:
        self.degraded = degraded

    async def recall(self, req: memory_pb2.RecallRequest) -> memory_pb2.RecallResponse:
        resp = build_recall_response(req)
        if self.degraded:
            resp.meta.degraded = True
        return resp

    async def write_episode(self, req: memory_pb2.WriteEpisodeRequest) -> str:
        del req
        return ""

    async def write_document(self, req: memory_pb2.WriteDocumentRequest) -> str:
        return req.document.doc_id


def _postgres_endpoint(dsn: str) -> tuple[str, int]:
    parsed = urlparse(dsn)
    return parsed.hostname or "localhost", parsed.port or 5432


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


async def build_backend(cfg: MemoryConfig):
    if cfg.backend == "canned":
        log.info("memory backend: canned")
        return CannedBackend()
    if cfg.backend == "agent":
        pg_host, pg_port = _postgres_endpoint(cfg.postgres_dsn)
        if not _port_open(cfg.qdrant_host, cfg.qdrant_port) or not _port_open(pg_host, pg_port):
            log.error(
                "agent memory needs qdrant %s:%s and postgres %s:%s, serving canned",
                cfg.qdrant_host,
                cfg.qdrant_port,
                pg_host,
                pg_port,
            )
            return CannedBackend(degraded=True)
        try:
            from .agent_backend import AgentBackend

            backend = AgentBackend(cfg)
            backend.open()
        except Exception:
            log.exception("agent memory init failed, serving canned")
            return CannedBackend(degraded=True)
        log.info("memory backend: agent collection prefix=%s", cfg.qdrant_collection)
        return backend
    if cfg.backend not in {"mem0", "graphiti"}:
        log.error("unknown MEMORY_BACKEND=%s, serving canned", cfg.backend)
        return CannedBackend(degraded=True)
    if not cfg.llm_api_key or not cfg.llm_model:
        log.error(
            "MEMORY_BACKEND=%s needs MEMORY_LLM_API_KEY and MEMORY_LLM_MODEL, serving canned",
            cfg.backend,
        )
        return CannedBackend(degraded=True)

    if cfg.backend == "mem0":
        if not _port_open(cfg.qdrant_host, cfg.qdrant_port):
            log.error(
                "qdrant %s:%s unreachable, serving canned",
                cfg.qdrant_host,
                cfg.qdrant_port,
            )
            return CannedBackend(degraded=True)
        try:
            from .mem0_backend import Mem0Backend

            backend = Mem0Backend(cfg)
        except Exception:
            log.exception("mem0 init failed, serving canned")
            return CannedBackend(degraded=True)
        log.info("memory backend: mem0 collection=%s", cfg.qdrant_collection)
        return backend

    if not _port_open(cfg.falkordb_host, cfg.falkordb_port):
        log.error(
            "falkordb %s:%s unreachable, serving canned",
            cfg.falkordb_host,
            cfg.falkordb_port,
        )
        return CannedBackend(degraded=True)
    try:
        from .graphiti_backend import GraphitiBackend

        backend = GraphitiBackend(cfg)
        await backend.open()
    except Exception:
        log.exception("graphiti init failed, serving canned")
        return CannedBackend(degraded=True)
    log.info("memory backend: graphiti %s:%s", cfg.falkordb_host, cfg.falkordb_port)
    return backend
