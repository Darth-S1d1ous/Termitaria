"""本地 JSON API。浏览器只跟这里说话，模型调用留在一条常驻事件循环上。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .converse import ChatDisabled, send_message
from .store import ProjectStore
from .types import ChatRow, Message, Project

log = logging.getLogger(__name__)


class ClientApp:
    def __init__(self, store: ProjectStore, model, max_tokens: int = 16384) -> None:
        self.store = store
        self.model = model
        self.max_tokens = max_tokens
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="clientapi-loop", daemon=True).start()

    def handle(self, method: str, path: str, body: dict | None) -> tuple[int, dict | None]:
        path = path.split("?", 1)[0].rstrip("/") or "/"
        try:
            return self._route(method.upper(), path, body or {})
        except KeyError:
            return 404, {"error": "not found"}
        except ChatDisabled as exc:
            return 409, {"error": str(exc)}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except Exception as exc:
            log.exception("client api %s %s", method, path)
            return 502, {"error": str(exc)}

    def _route(self, method: str, path: str, body: dict) -> tuple[int, dict | None]:
        if method == "GET" and path == "/api/health":
            return 200, {"ok": True}
        if method == "GET" and path == "/api/projects":
            return 200, {"projects": [_project_json(item) for item in self.store.list_projects()]}
        if method == "POST" and path == "/api/projects":
            project = self.store.create_project(str(body.get("name", "")), goal=str(body.get("goal", "")))
            return 201, _project_json(project)

        one = re.fullmatch(r"/api/projects/([^/]+)", path)
        if one and method == "DELETE":
            self.store.delete_project(one.group(1))
            return 204, None

        chats = re.fullmatch(r"/api/projects/([^/]+)/chats", path)
        if chats and method == "GET":
            return 200, {"chats": [_chat_json(item) for item in self.store.list_chats(chats.group(1))]}

        messages = re.fullmatch(r"/api/projects/([^/]+)/chats/([^/]+)/messages", path)
        if messages and method == "GET":
            history = self.store.list_messages(messages.group(1), messages.group(2))
            return 200, {"messages": [_message_json(item) for item in history]}
        if messages and method == "POST":
            text = str(body.get("text", ""))
            if not text.strip():
                raise ValueError("text is required")
            user, agent = self._run(
                send_message(
                    self.store,
                    messages.group(1),
                    messages.group(2),
                    text,
                    self.model,
                    self.max_tokens,
                )
            )
            return 200, {"user": _message_json(user), "agent": _message_json(agent)}
        return 404, {"error": "not found"}

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()


def serve(app: ClientApp, host: str, port: int) -> None:
    handler = _handler_for(app)
    httpd = ThreadingHTTPServer((host, port), handler)
    log.info("client api http://%s:%s", host, port)
    httpd.serve_forever()


def _handler_for(app: ClientApp):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_DELETE(self) -> None:
            self._dispatch()

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._cors()
            self.end_headers()

        def _dispatch(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw.decode()) if raw else None
            status, payload = app.handle(self.command, self.path, body)
            data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self._cors()
            if data:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s - " + fmt, self.address_string(), *args)

    return Handler


def _project_json(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "goal": project.goal,
        "agents": [
            {"id": agent.id, "display_name": agent.display_name, "kind": agent.kind}
            for agent in project.agents
        ],
    }


def _chat_json(chat: ChatRow) -> dict:
    return {
        "id": chat.id,
        "kind": chat.kind,
        "title": chat.title,
        "agent_kind": chat.agent_kind,
        "pinned": chat.pinned,
        "enabled": chat.enabled,
        "last_message": chat.last_message,
        "last_role": chat.last_role,
        "last_message_at_ms": chat.last_message_at_ms,
    }


def _message_json(message: Message) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "text": message.text,
        "created_at_ms": message.created_at_ms,
    }
