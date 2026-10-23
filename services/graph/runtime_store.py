"""选择图存储。FalkorDB 通着就用它，一个 project 一张图。否则退回进程内的图。"""
from __future__ import annotations

import logging
import socket

from .canned import install
from .config import GraphConfig
from .falkor_store import FalkorStore, parse_falkor_url
from .store import InMemoryGraph

log = logging.getLogger(__name__)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


async def open_store(cfg: GraphConfig):
    if cfg.load_canned:
        store = InMemoryGraph()
        await install(store)
        log.info("graph store: canned")
        return store
    host, port = parse_falkor_url(cfg.falkordb_url)
    if _port_open(host, port):
        try:
            store = FalkorStore(host, port)
            store.ping()
        except Exception:
            log.exception("falkordb init failed, using an in-memory graph")
        else:
            log.info("graph store: falkordb %s:%s", host, port)
            return store
    else:
        log.error("falkordb %s:%s unreachable, using an in-memory graph", host, port)
    return InMemoryGraph()
