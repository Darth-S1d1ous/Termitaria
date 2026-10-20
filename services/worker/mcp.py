"""最小 MCP 客户端。只支持 HTTP JSON-RPC：initialize、tools/list、tools/call。

配置文件 mcp.json：
{"servers":[{"name":"docs","url":"http://127.0.0.1:3001/mcp"}]}
没有这个文件就不注册任何 MCP 工具。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


@dataclass
class MCPTool:
    server: str
    name: str
    description: str
    url: str
    session_id: str


def load_mcp_tools(path: Path) -> list[MCPTool]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("mcp config %s: %s", path, exc)
        return []
    tools: list[MCPTool] = []
    for server in raw.get("servers") or []:
        name = str(server.get("name") or "").strip()
        url = str(server.get("url") or "").strip()
        if not name or not url:
            continue
        try:
            tools.extend(_list_tools(name, url))
        except Exception as exc:
            log.warning("mcp %s: %s", name, exc)
    return tools


def call_mcp(tool: MCPTool, arguments: dict) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": tool.name, "arguments": arguments},
    }
    data = _post(tool.url, payload, tool.session_id)
    result = data.get("result") if isinstance(data, dict) else None
    if isinstance(data, dict) and data.get("error"):
        return json.dumps(data["error"], ensure_ascii=False)
    if not isinstance(result, dict):
        return json.dumps(data, ensure_ascii=False)[:4000]
    content = result.get("content")
    if isinstance(content, list):
        texts = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
        joined = "\n".join(part for part in texts if part).strip()
        if joined:
            return joined[:8000]
    return json.dumps(result, ensure_ascii=False)[:4000]


def _list_tools(server: str, url: str) -> list[MCPTool]:
    init = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "termitaria", "version": "0.1.0"},
            },
        },
        "",
    )
    session_id = str(init.get("_session") or "")
    listed = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session_id)
    result = listed.get("result") if isinstance(listed, dict) else {}
    found: list[MCPTool] = []
    for item in (result or {}).get("tools") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        found.append(
            MCPTool(
                server=server,
                name=str(item["name"]),
                description=str(item.get("description") or ""),
                url=url,
                session_id=session_id,
            )
        )
    return found


def _post(url: str, payload: dict, session_id: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urlopen(req, timeout=20) as res:
        body = res.read().decode("utf-8", "replace")
        sid = res.headers.get("Mcp-Session-Id") or session_id
    data = _decode(body)
    if isinstance(data, dict) and sid:
        data["_session"] = sid
    return data


def _decode(body: str) -> dict:
    body = body.strip()
    if body.startswith("{"):
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    # 有的服务用 SSE：多行 data: {...}
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line.startswith("{"):
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                return parsed
    return {}
