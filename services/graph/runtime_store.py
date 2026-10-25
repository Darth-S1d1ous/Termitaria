"""打开 Graphiti。FalkorDB 或模型没配上时，入库只记日志，不编一张假图。"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from .config import GraphConfig
from .graphiti_kg import GraphitiKnowledge, IdleKnowledge, open_graphiti

log = logging.getLogger(__name__)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _falkor(url: str) -> tuple[str, int]:
    parsed = urlparse(url if "://" in url else "redis://" + url)
    return parsed.hostname or "localhost", parsed.port or 6379


async def open_engine(cfg: GraphConfig):
    host, port = _falkor(cfg.falkordb_url)
    if not _port_open(host, port):
        log.error("falkordb %s:%s unreachable", host, port)
        return IdleKnowledge()
    engine = open_graphiti(host, port)
    if isinstance(engine, GraphitiKnowledge):
        try:
            await engine.open()
        except Exception:
            log.exception("graphiti init failed")
            return IdleKnowledge()
    return engine
