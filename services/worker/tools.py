"""工具。模型用一行 JSON 调用，跑完把结果塞回窗口，再决定要不要继续。

形状靠近 CrewAI 的 BaseTool：名字、一句话说明、一份参数、一次执行。
delegate 对应 CrewAI 的 Delegate work to coworker，只是同事跑在另一条 session 上。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .calls import Call, extract_tool_call
from .mcp import MCPTool, call_mcp
from .skills import Skill

Runner = Callable[[dict], Awaitable[str]]

_ALIASES = {
    "ledger": "plan",
    "save": "write_file",
    "search": "web_search",
    "fetch": "fetch_url",
}


@dataclass
class Tool:
    name: str
    description: str
    run: Runner
    parameters: dict | None = None
    required: list | None = None

    def __post_init__(self) -> None:
        if self.parameters is None:
            self.parameters = {}
        if self.required is None:
            self.required = []


class Toolbox:
    def __init__(self, tools: list[Tool]):
        self.tools = {tool.name: tool for tool in tools}

    def names(self) -> list[str]:
        return list(self.tools)

    def instructions(self) -> str:
        if not self.tools:
            return ""
        lines = [
            "When you need to look something up, delegate, or write a file, call the function you were given.",
            "If this request includes functions, use a function call. Do not put the call in the text the user sees.",
            "When you have enough information, reply in plain English and do not call another function.",
            "If this request has no functions and you still must use a tool, reply with JSON only, one object per line:",
            '{"tool":"tool_name","param":"value"}',
        ]
        for tool in self.tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    def schemas(self) -> list[dict]:
        found = []
        for tool in self.tools.values():
            found.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": tool.parameters,
                            "required": tool.required,
                        },
                    },
                }
            )
        return found

    async def run(self, name: str, arguments: dict) -> str:
        name = _ALIASES.get(name, name)
        tool = self.tools.get(name)
        if tool is None:
            return f"No tool named {name}. Available: {', '.join(self.tools) or 'none'}"
        try:
            return await tool.run(arguments)
        except Exception as exc:
            return f"{name} failed: {exc}"


def visible_text(text: str) -> str:
    """去掉推理，只留模型真正要说的话。

    规则和 ChatNVIDIA 的 parse_thinking_content 一致：成对的 <think>，
    或者只有 </think> 时，标签前面整段都是推理。
    库的函数不处理被截断、没有 </think> 的情况，那种正文还没写出来，这里直接丢掉。
    """
    if not text:
        return ""
    if re.search(r"<think\b", text, re.IGNORECASE) and not re.search(r"</think>", text, re.IGNORECASE):
        return ""
    from langchain_nvidia_ai_endpoints.chat_models import parse_thinking_content

    _, _, clean = parse_thinking_content(text, remove_tags=True)
    return clean.strip()


def parse_tool_calls(text: str) -> list[Call]:
    """正文里的 JSON 是没有原生工具调用时的退路。"""
    decoder = json.JSONDecoder()
    found: list[Call] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        call = extract_tool_call(obj) if isinstance(obj, dict) else None
        if call is not None:
            found.append(call)
            index = end
        else:
            index = start + 1
    return found


def mention_delegates(text: str, coworkers: list[dict]) -> list[Call]:
    """正文里的 @id / @显示名。模型没走工具时，当成一次 delegate。"""
    calls: list[Call] = []
    seen: set[str] = set()
    for person in coworkers:
        agent_id = person["id"]
        if agent_id in seen:
            continue
        names = [agent_id]
        display = (person.get("display_name") or "").strip()
        if display:
            names.append(display)
        if any(re.search(rf"@{re.escape(name)}\b", text) for name in names):
            seen.add(agent_id)
            calls.append(
                Call(
                    name="delegate",
                    arguments={"coworker": agent_id, "task": text, "context": ""},
                )
            )
    return calls


def make_toolbox(
    *,
    agent: dict,
    project_id: str,
    steps: list[dict],
    skills: list[Skill],
    mcp_tools: list[MCPTool],
    gateway: str,
) -> Toolbox:
    listed = [item for item in (agent.get("tools") or []) if item]
    names = set(listed)
    kind = agent.get("kind") or "WORKER"
    if not listed:
        if kind == "ORCHESTRATOR":
            names.update(["delegate", "plan"])
        else:
            names.update(["web_search", "fetch_url", "write_file"])
    if "web_search" in names:
        names.add("fetch_url")
        names.add("utc_time")
    if kind == "ORCHESTRATOR" or "delegate" in names or "ledger" in names:
        names.update(["delegate", "plan"])
    tools: list[Tool] = []
    if "web_search" in names:
        tools.append(Tool(
            "web_search",
            "Search the web for query. Return titles, links, and a short snippet.",
            _web_search,
            {"query": {"type": "string", "description": "search query"}},
            ["query"],
        ))
    if "fetch_url" in names:
        tools.append(Tool(
            "fetch_url",
            "Open url and return the page text. PDFs are extracted when possible.",
            _fetch_url,
            {"url": {"type": "string", "description": "http or https URL"}},
            ["url"],
        ))
    if "utc_time" in names:
        tools.append(Tool(
            "utc_time",
            "Convert a date and unix seconds. date is YYYY-MM-DD at 00:00 UTC. unix is seconds, converted back to a UTC date. Do not calculate this yourself.",
            _utc_time,
            {
                "date": {"type": "string", "description": "YYYY-MM-DD at 00:00 UTC"},
                "unix": {"type": "integer", "description": "unix seconds"},
            },
        ))
    if "write_file" in names:
        tools.append(Tool(
            "write_file",
            "Write content to path inside the working directory, for example notes.md.",
            _write_file,
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            ["path", "content"],
        ))
    if "delegate" in names:
        tools.append(
            Tool(
                "delegate",
                "Give a task to a coworker. coworker is an id or display name, task is what to do, and context is material you already have. "
                "The coworker finishes in their own session and the result comes back here.",
                _delegate(gateway, project_id, agent.get("id") or ""),
                {
                    "coworker": {"type": "string", "description": "coworker id or display name"},
                    "task": {"type": "string"},
                    "context": {"type": "string"},
                },
                ["coworker", "task"],
            )
        )
    if "plan" in names:
        labels = ", ".join(step.get("label") or step.get("id") or "" for step in steps if step.get("label") or step.get("id"))
        hint = f"Current steps: {labels}. " if labels else "Use the step label as label. "
        tools.append(
            Tool(
                "plan",
                hint + "Arguments are label and state (pending, active, done, failed). Mark a step done when it is finished.",
                _plan(gateway, project_id),
                {
                    "label": {"type": "string"},
                    "state": {"type": "string", "enum": ["pending", "active", "done", "failed"]},
                },
                ["label", "state"],
            )
        )
    if "kg_query" in names or "kg_write" in names or "kg" in names:
        tools.append(Tool("kg", "The knowledge graph is not connected.", _kg))
    if skills:
        by_name = {skill.name: skill for skill in skills}
        tools.append(
            Tool(
                "load_skill",
                "Load the full text of a skill. name is one of: " + ", ".join(by_name) + ".",
                _load_skill(by_name),
                {"name": {"type": "string", "enum": list(by_name)}},
                ["name"],
            )
        )
    for remote in mcp_tools:
        tools.append(
            Tool(
                f"mcp_{remote.server}_{remote.name}",
                f"MCP {remote.server}/{remote.name}. {remote.description}".strip(),
                _mcp(remote),
            )
        )
    return Toolbox(tools)


def _delegate(gateway: str, project_id: str, agent_id: str) -> Runner:
    async def run(args: dict) -> str:
        coworker = str(args.get("coworker") or args.get("role") or "").strip()
        task = str(args.get("task") or "").strip()
        context = str(args.get("context") or "")
        if not coworker or not task:
            return "delegate requires coworker and task"
        status, body = await asyncio.to_thread(
            _post_json,
            f"{gateway}/api/projects/{project_id}/delegate",
            {"from": agent_id, "coworker": coworker, "task": task, "context": context},
            26 * 60,
        )
        if status != 200:
            return f"delegate {coworker} did not finish (HTTP {status}): {body[:500]}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return body[:4000]
        who = data.get("agent_id") or coworker
        return f"{who} replied:\n{data.get('text') or ''}"

    return run


def _plan(gateway: str, project_id: str) -> Runner:
    async def run(args: dict) -> str:
        label = str(args.get("label") or args.get("step") or "").strip()
        state = str(args.get("state") or "done").strip()
        if not label:
            return "plan requires label"
        status, body = await asyncio.to_thread(
            _post_json,
            f"{gateway}/api/projects/{project_id}/steps",
            {"label": label, "state": state},
            15,
        )
        if status != 200:
            return f"plan was not updated (HTTP {status}): {body[:300]}"
        return f"Step {label!r} is now {state}"

    return run


def _load_skill(by_name: dict[str, Skill]) -> Runner:
    async def run(args: dict) -> str:
        name = str(args.get("name") or "").strip()
        skill = by_name.get(name)
        if skill is None:
            return "No such skill. Choices: " + ", ".join(by_name)
        return skill.body

    return run


def _mcp(remote: MCPTool) -> Runner:
    async def run(args: dict) -> str:
        return await asyncio.to_thread(call_mcp, remote, args)

    return run


async def _kg(_: dict) -> str:
    return "The knowledge graph is not connected. Do not pretend you queried it."


async def _web_search(args: dict) -> str:
    query = str(args.get("query") or args.get("q") or "").strip()
    if not query:
        return "web_search requires query"
    return await asyncio.to_thread(_search_blocking, query)


async def _utc_time(args: dict) -> str:
    date = str(args.get("date") or "").strip()
    raw_unix = args.get("unix")
    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return "utc_time date must be YYYY-MM-DD"
        return f"{date} 00:00 UTC = {int(day.timestamp())}"
    if raw_unix not in (None, ""):
        try:
            ts = int(float(str(raw_unix)))
        except ValueError:
            return "utc_time unix must be seconds"
        day = datetime.fromtimestamp(ts, timezone.utc)
        return f"{ts} = {day:%Y-%m-%d} {day:%H:%M} UTC"
    return "utc_time requires date or unix"


async def _fetch_url(args: dict) -> str:
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return "fetch_url only accepts http or https"
    return await asyncio.to_thread(_fetch_blocking, url)


async def _write_file(args: dict) -> str:
    path = str(args.get("path") or "").strip()
    content = args.get("content")
    if not path or content is None:
        return "write_file requires path and content"
    return _write_blocking(path, str(content))


def _write_blocking(path: str, content: str) -> str:
    root = Path(os.environ.get("WORK_DIR", os.getcwd())).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    if target != root and root not in target.parents:
        return f"Refused to write {target}. Only paths inside {root} are allowed."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {target} ({len(content)} characters)."


def _search_blocking(query: str) -> str:
    ddg = _duckduckgo(query)
    if ddg and "no parsed results" not in ddg:
        return ddg
    bing = _bing(query)
    if bing:
        return bing
    return ddg or "The search returned no results."


def _duckduckgo(query: str) -> str:
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    try:
        html = _http_text(url, 20)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return f"DuckDuckGo search failed: {exc}"
    hits = _pairs(html, "result__a", "result__snippet")
    return _format_hits("DuckDuckGo", hits) if hits else "DuckDuckGo returned no parsed results."


def _bing(query: str) -> str:
    url = "https://www.bing.com/search?" + urlencode({"q": query})
    try:
        html = _http_text(url, 20)
    except (HTTPError, URLError, TimeoutError, OSError):
        return ""
    hits = _pairs(html, "b_title", "")
    if not hits:
        return ""
    return _format_hits("Bing", hits)


def _format_hits(source: str, hits: list[tuple[str, str, str]]) -> str:
    lines = [f"Source {source}, {len(hits)} hits. Open a page with fetch_url."]
    for title, link, snippet in hits[:5]:
        lines.append(f"- {title}\n  {link}\n  {snippet}".rstrip())
    return "\n".join(lines)


class _LinkGrabber(HTMLParser):
    def __init__(self, title_class: str, snippet_class: str):
        super().__init__()
        self.title_class = title_class
        self.snippet_class = snippet_class
        self.hits: list[tuple[str, str, str]] = []
        self._href = ""
        self._capture = ""
        self._buf: list[str] = []
        self._pending: tuple[str, str] | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        classes = attr.get("class", "")
        if tag == "a" and self.title_class in classes.split():
            self._href = attr.get("href", "")
            self._capture = "title"
            self._buf = []
        elif self.snippet_class and self.snippet_class in classes.split():
            self._capture = "snippet"
            self._buf = []

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._capture == "title" and tag == "a":
            title = " ".join("".join(self._buf).split())
            if title and self._href:
                self._pending = (title, self._href)
                if not self.snippet_class:
                    self.hits.append((title, self._href, ""))
                    self._pending = None
            self._capture = ""
        elif self._capture == "snippet" and tag in {"a", "td", "div", "span", "p"}:
            snippet = " ".join("".join(self._buf).split())
            if self._pending:
                self.hits.append((self._pending[0], self._pending[1], snippet))
                self._pending = None
            self._capture = ""


def _pairs(html: str, title_class: str, snippet_class: str) -> list[tuple[str, str, str]]:
    parser = _LinkGrabber(title_class, snippet_class)
    parser.feed(html)
    return parser.hits


def _fetch_blocking(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Termitaria/0.1"})
    with urlopen(req, timeout=25) as res:
        data = res.read(1_000_000)
        ctype = res.headers.get("Content-Type", "")
    if url.lower().split("?", 1)[0].endswith(".pdf") or "pdf" in ctype.lower() or data.startswith(b"%PDF"):
        text = _pdf_text(data)
        return f"PDF {url}\n{text or 'No text extracted. The link itself can be cited.'}"
    html = data.decode("utf-8", "replace")
    text = _html_text(html)
    if not text:
        return f"Opened {url}, but there was no readable text."
    return text[:8000]


def _pdf_text(data: bytes) -> str:
    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(
                ["pdftotext", "-q", "-", "-"],
                input=data,
                capture_output=True,
                timeout=15,
                check=False,
            )
            text = out.stdout.decode("utf-8", "replace").strip()
            if text:
                return text[:8000]
        except (OSError, subprocess.TimeoutExpired):
            pass
    parts = re.findall(rb"\((?:\\.|[^\\)]){8,200}\)", data)
    lines = []
    for part in parts[:80]:
        raw = part[1:-1].replace(b"\\n", b"\n").replace(b"\\(", b"(").replace(b"\\)", b")")
        line = raw.decode("latin1", "replace").strip()
        if line:
            lines.append(line)
    return "\n".join(lines)[:8000]


def _html_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p>", "\n", html)
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    return "\n".join(line.strip() for line in html.splitlines() if line.strip())


def _http_text(url: str, timeout: int) -> str:
    return _fetch_head(url, timeout).decode("utf-8", "replace")


def _fetch_head(url: str, timeout: int) -> bytes:
    req = Request(url, headers={"User-Agent": "Termitaria/0.1"})
    with urlopen(req, timeout=timeout) as res:
        return res.read(500_000)


def _post_json(url: str, payload: dict, timeout: int) -> tuple[int, str]:
    req = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as res:
            return res.status, res.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def load_project(gateway: str, project_id: str) -> dict | None:
    if not gateway or not project_id:
        return None
    try:
        with urlopen(f"{gateway}/api/projects", timeout=1.5) as res:
            data = json.loads(res.read().decode())
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    for project in data.get("projects") or []:
        if project.get("id") == project_id:
            return project
    return None
