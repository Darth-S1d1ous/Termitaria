"""本机 HTTP：GET /graph?agent= 把当前后端的图交给 gateway。"""
from __future__ import annotations

import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .graph_view import agent_ok, empty_graph_view

log = logging.getLogger(__name__)


async def load_graph(backend, agent_id: str) -> dict:
    name = getattr(backend, "name", "")
    if not agent_ok(agent_id):
        return empty_graph_view(name, agent_id)
    view = getattr(backend, "graph_view", None)
    if view is None:
        return empty_graph_view(name, agent_id)
    return await view(agent_id)


def serve_graph(backend, addr: str, loop: asyncio.AbstractEventLoop) -> None:
    host, _, port = addr.rpartition(":")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/graph":
                self._send(404, {"error": "not found"})
                return
            agent = parse_qs(parsed.query).get("agent", [""])[0].strip()
            future = asyncio.run_coroutine_threadsafe(load_graph(backend, agent), loop)
            try:
                payload = future.result(timeout=10)
            except Exception as exc:
                log.exception("graph http failed")
                self._send(502, {"error": str(exc)})
                return
            self._send(200, payload)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            log.info("graph http " + fmt, *args)

    server = None
    try:
        server = ThreadingHTTPServer((host, int(port)), Handler)
    except OSError:
        log.exception("graph http failed to listen on %s", addr)
        return
    log.info("graph http http://%s/graph", addr)
    server.serve_forever()
