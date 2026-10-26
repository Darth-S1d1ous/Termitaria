"""工具。模型用一行 JSON 调用，跑完把结果塞回窗口，再决定要不要继续。

形状靠近 CrewAI 的 BaseTool：名字、一句话说明、一份参数、一次执行。
delegate 对应 CrewAI 的 Delegate work to coworker：负责人在这一次执行里调用同事，同事不能再派活。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .calls import Call, extract_tool_call
from .deck import build_pptx
from .mcp import MCPTool, call_mcp
from .skills import Skill

Runner = Callable[[dict], Awaitable[str]]

log = logging.getLogger(__name__)

_ALIASES = {
    "ledger": "advance",
    "plan": "advance",
    "save": "write_file",
    "search": "web_search",
    "fetch": "fetch_url",
}

# 子模型始终带着这套工具。SwarmSpec 的 tools 只能再加，不能把这套拿掉。
# delegate 不在里面。只有负责人能派活。
_WORKER_BASE = ("web_search", "fetch_url", "utc_time", "write_file")
_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*$")


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
            "When you have enough information, answer the user and do not call another function.",
            "If this request has no functions and you still must use a tool, reply with JSON only, one object per line:",
            '{"tool":"tool_name","param":"value"}',
        ]
        if "ask_user" in self.tools:
            lines.append(
                "While a step is active, call delegate, advance, write_report, or ask_user. "
                "Call write_deck too when the report tool says a deck is required. A plain reply does not finish the turn."
            )
            lines.append(
                "ask_user needs known and question. write_report must be stored before the last step can turn done."
            )
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


def materials_text(seeds: list | None) -> str:
    lines = []
    for seed in seeds or []:
        if not isinstance(seed, dict):
            continue
        title = str(seed.get("title") or "").strip()
        body = str(seed.get("body") or "").strip()
        kind = str(seed.get("kind") or "text")
        if not title and not body:
            continue
        lines.append(f"- ({kind}) {title}\n  {body}".rstrip())
    if not lines:
        return ""
    return "Project materials (cite these as the project seed):\n" + "\n".join(lines)


def fresh_uploads(seeds: list | None, seen: set[str]) -> list[dict]:
    """本轮开始时还没进提示词的文件。seen 里的 id 不再重复送。"""
    found = []
    for seed in seeds or []:
        if not isinstance(seed, dict):
            continue
        sid = str(seed.get("id") or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        title = str(seed.get("title") or "").strip()
        body = str(seed.get("body") or "").strip()
        if not title and not body:
            continue
        found.append({"id": sid, "title": title or sid, "body": body})
    return found


def upload_notice(seeds: list[dict]) -> str:
    blocks = []
    for seed in seeds:
        title = str(seed.get("title") or "file").strip() or "file"
        body = str(seed.get("body") or "").strip()
        blocks.append(f"File: {title}\n{body}")
    return (
        "The user uploaded a file into this project's knowledge graph while you were working. "
        "Read the file below. Call kg with action query to read the new entities and relations. "
        "If that query does not list this file yet, call kg query again before you answer.\n\n"
        + "\n\n".join(blocks)
    )


def uploaded_files(project: dict | None) -> str:
    lines = []
    for seed in (project or {}).get("seeds") or []:
        if not isinstance(seed, dict):
            continue
        sid = str(seed.get("id") or "")
        if not sid.startswith("u_"):
            continue
        title = str(seed.get("title") or sid).strip() or sid
        body = str(seed.get("body") or "").strip()
        if len(body) > 4000:
            body = body[:4000] + "…"
        lines.append(f"- {title}\n  {body}".rstrip())
    if not lines:
        return ""
    return "Files the user uploaded into this knowledge graph:\n" + "\n".join(lines)


def make_toolbox(
    *,
    agent: dict,
    project_id: str,
    steps: list[dict],
    skills: list[Skill],
    mcp_tools: list[MCPTool],
    gateway: str,
    seeds: list | None = None,
    deliverable: dict | None = None,
    sources: set | None = None,
    delegate_run: Callable[[str, str, str], Awaitable[str]] | None = None,
) -> Toolbox:
    listed = [item for item in (agent.get("tools") or []) if item]
    names = set(listed)
    kind = agent.get("kind") or "WORKER"
    if not listed:
        if kind == "ORCHESTRATOR":
            names.update(["delegate", "advance"])
        else:
            names.update(["web_search", "fetch_url", "write_file"])
    if kind != "ORCHESTRATOR":
        names.update(_WORKER_BASE)
        names.discard("delegate")
    if "web_search" in names:
        names.add("fetch_url")
        names.add("utc_time")
    if kind == "ORCHESTRATOR" or "ledger" in listed:
        names.update(["delegate", "advance"])
    if kind == "ORCHESTRATOR":
        names.update(["ask_user", "write_report", "write_deck"])
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
            "Open url. For an arXiv paper or a PDF, the body is split into sections. "
            "Pass section to read one heading, such as Datasets. "
            "Without section, the tool returns the section list and the first section. "
            "If the body was not extracted, the result says the paper was not read.",
            _fetch_url,
            {
                "url": {"type": "string", "description": "http or https URL"},
                "section": {"type": "string", "description": "heading to read, for a paper"},
            },
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
    if kind == "ORCHESTRATOR" and "delegate" in names:
        tools.append(
            Tool(
                "delegate",
                "Give a task to a coworker. coworker is an id or display name, task is what to do, and context is material you already have. "
                "The coworker runs now, in this turn, and cannot delegate. "
                "The reply lists Tools that ran, Steps changed, and Files written. Only those lines record what happened.",
                _delegate(delegate_run, materials_text(seeds)),
                {
                    "coworker": {"type": "string", "description": "coworker id or display name"},
                    "task": {"type": "string"},
                    "context": {"type": "string"},
                },
                ["coworker", "task"],
            )
        )
    if "advance" in names:
        labels = ", ".join(step.get("label") or step.get("id") or "" for step in steps if step.get("label") or step.get("id"))
        hint = f"Current steps: {labels}. " if labels else "Use the step label as label. "
        tools.append(
            Tool(
                "advance",
                hint + "Arguments are label and state (pending, active, done, failed). "
                "The gateway keeps the last step open until the report is stored, and until a deck is stored when the project requires one.",
                _plan(gateway, project_id),
                {
                    "label": {"type": "string"},
                    "state": {"type": "string", "enum": ["pending", "active", "done", "failed"]},
                },
                ["label", "state"],
            )
        )
    if "ask_user" in names:
        tools.append(
            Tool(
                "ask_user",
                "Stop and ask the user. known is what you already have, with sources. question is one specific question.",
                _ask_user,
                {
                    "known": {"type": "string"},
                    "question": {"type": "string"},
                },
                ["known", "question"],
            )
        )
    if "write_report" in names:
        tools.append(
            Tool(
                "write_report",
                _report_description(deliverable),
                _write_report(gateway, project_id, deliverable, sources),
                {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": "Markdown report. Each required section is a ## heading.",
                    },
                },
                ["body"],
            )
        )
    if "write_deck" in names:
        tools.append(
            Tool(
                "write_deck",
                "Store a pptx on the project. slides is a list of {title, bullets}. At least 3 slides.",
                _write_deck(gateway, project_id),
                {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "slides": {"type": "array"},
                },
                ["title", "slides"],
            )
        )
    if "kg_query" in names or "kg_write" in names or "kg" in names:
        tools.append(
            Tool(
                "kg",
                "Read or write this project's knowledge graph. action is query or write. "
                "A write queues title and text for extraction. The entities and relations show up on a later query. "
                "Query lists those entities and each relation's name and fact, plus files the user uploaded. "
                "If the user uploads a file while you are working, query again to read that file and its relations.",
                _kg(project_id, gateway),
                {
                    "action": {"type": "string", "description": "query or write"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                },
                ["action"],
            )
        )
    if any(skill.name == "filings" for skill in skills) or "edgar" in names:
        tools.append(
            Tool(
                "edgar",
                "Find a US company's latest 10-K, 10-Q, or 8-K on SEC EDGAR. company is a ticker or a legal name. Companies outside EDGAR are not here.",
                _edgar,
                {"company": {"type": "string", "description": "ticker or legal name"}},
                ["company"],
            )
        )
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


def _delegate(run: Callable[[str, str, str], Awaitable[str]] | None, materials: str) -> Runner:
    async def go(args: dict) -> str:
        coworker = str(args.get("coworker") or args.get("role") or "").strip()
        task = str(args.get("task") or "").strip()
        context = str(args.get("context") or "")
        if materials and materials not in context:
            context = (context + "\n\n" + materials).strip()
        if not coworker or not task:
            return "delegate requires coworker and task"
        if run is None:
            return "delegate is not available in this turn"
        return await run(coworker, task, context)

    return go


def _plan(gateway: str, project_id: str) -> Runner:
    async def run(args: dict) -> str:
        label = str(args.get("label") or args.get("step") or "").strip()
        state = str(args.get("state") or "done").strip()
        if not label:
            return "advance requires label"
        status, body = await asyncio.to_thread(
            _post_json,
            f"{gateway}/api/projects/{project_id}/steps",
            {"label": label, "state": state},
            15,
        )
        if status != 200:
            return f"advance was not applied (HTTP {status}): {body[:500]}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return f"Step {label!r} is now {state}"
        summary = "; ".join(
            f"{step.get('label') or step.get('id')} [{step.get('state')}]"
            for step in data.get("steps") or []
        )
        return f"Step {label!r} is now {state}\nsteps: {summary}"

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


def _kg(project_id: str, gateway: str = "") -> Runner:
    async def run(args: dict) -> str:
        action = str(args.get("action") or "query").strip().lower()
        log.info("tool kg action=%s", action)
        from services.graph.client import GraphClient

        # 写入只入队。查询只读已经落库的实体，并带上用户刚传的文件。
        timeout = 10 if action == "write" else 4
        client = GraphClient(os.environ.get("GRAPH_GRPC_ADDR", "localhost:50051"), timeout_s=timeout)
        try:
            if action == "write":
                return await _kg_write(client, project_id, args)
            graph = await _kg_read(client, project_id)
            project = await asyncio.to_thread(load_project, gateway, project_id)
            files = uploaded_files(project)
            if files:
                return f"{files}\n\n{graph}"
            return graph
        except Exception as exc:
            return f"The knowledge graph is not reachable: {exc}"
        finally:
            await client.close()

    return run


async def _kg_read(client, project_id: str) -> str:
    from termitaria.graph.v1 import graph_pb2

    resp = await client.query_subgraph([f"{project_id}/root"], depth=4, max_nodes=40, strict=True)
    titles = {node.node_id: node.title or node.node_id for node in resp.nodes}
    lines = []
    for node in resp.nodes:
        if node.node_id == f"{project_id}/root":
            continue
        kind = graph_pb2.NodeKind.Name(node.kind).removeprefix("NODE_KIND_").lower()
        text = ""
        if node.props and "text" in node.props:
            text = str(node.props["text"])
        line = f"{kind}: {node.title}"
        if text and text != node.title:
            line = f"{line} — {text[:240]}"
        lines.append(line)
    relations = []
    for edge in resp.edges:
        relation = edge.relation.strip()
        if not relation:
            continue
        fact = edge.fact.strip()
        line = f"{relation}: {titles.get(edge.from_node_id, edge.from_node_id)} -> {titles.get(edge.to_node_id, edge.to_node_id)}"
        if fact:
            line = f"{line} — {fact[:240]}"
        relations.append(line)
    if not lines and not relations:
        return "This project's knowledge graph has no nodes yet."
    parts = []
    if lines:
        parts.append("Knowledge graph:\n" + "\n".join(f"- {line}" for line in lines))
    if relations:
        parts.append("Relations:\n" + "\n".join(f"- {line}" for line in relations))
    return "\n".join(parts)


async def _kg_write(client, project_id: str, args: dict) -> str:
    from google.protobuf.struct_pb2 import Struct

    from termitaria.graph.v1 import graph_pb2

    title = str(args.get("title") or "").strip()
    text = str(args.get("text") or title).strip()
    if not title:
        return "kg write requires title"
    digest = hashlib.sha256(f"{project_id}\n{title}\n{text}".encode()).hexdigest()[:12]
    node_id = f"{project_id}/note_{digest}"
    props = Struct()
    props.update({"text": text[:2000]})
    await client.upsert_node(
        graph_pb2.Node(
            node_id=node_id,
            kind=graph_pb2.NODE_KIND_CONCEPT,
            title=title[:200],
            props=props,
        )
    )
    return (
        "Queued this text for the project knowledge graph. "
        "A later query can see the extracted entities and relations."
    )


async def _web_search(args: dict) -> str:
    query = str(args.get("query") or args.get("q") or "").strip()
    log.info("tool web_search query=%s", query[:160])
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


_ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf|html|ftp)/([0-9]{4}\.[0-9]{4,5})(v[0-9]+)?", re.I)
_PAPER_SECTION = 12000
_TEXT_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][A-Za-z0-9 ,:'\-]{2,80}$")


async def _fetch_url(args: dict) -> str:
    url = str(args.get("url") or "").strip()
    section = str(args.get("section") or "").strip()
    log.info("tool fetch_url url=%s section=%s", url[:180], section[:80])
    if not url.startswith(("http://", "https://")):
        return "fetch_url only accepts http or https"
    return await asyncio.to_thread(_fetch_blocking, url, section)


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


def _search_key() -> str:
    key = os.environ.get("SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY") or ""
    if key:
        return key
    from .config import _secret

    return _secret("brave-secrets", "SEARCH_API_KEY")


def _search_blocking(query: str) -> str:
    key = _search_key()
    if not key:
        return "web_search failed: SEARCH_API_KEY is not set"
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": "5"})
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
            "User-Agent": "Termitaria/0.1",
        },
    )
    try:
        with urlopen(req, timeout=20) as res:
            status = res.status
            raw = res.read(500_000)
    except HTTPError as exc:
        detail = exc.read(300).decode("utf-8", "replace")
        return f"web_search failed: HTTP {exc.code} {detail}".strip()
    except (URLError, TimeoutError, OSError) as exc:
        return f"web_search failed: {exc}"
    if status != 200:
        return f"web_search failed: HTTP {status}"
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return "web_search failed: Brave did not return JSON"
    hits = []
    for item in (data.get("web") or {}).get("results") or []:
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or "").strip()
        snippet = str(item.get("description") or "").strip()
        if title and link:
            hits.append((title, link, snippet))
    if not hits:
        return "web_search failed: Brave returned no results"
    lines = [f"Source Brave, {len(hits[:5])} hits. Open a page with fetch_url."]
    for title, link, snippet in hits[:5]:
        lines.append(f"- {title}\n  {link}\n  {snippet}".rstrip())
    return "\n".join(lines)


def _fetch_blocking(url: str, section: str = "") -> str:
    paper = _ARXIV_ID.search(url)
    if paper is not None:
        return _fetch_arxiv(paper.group(1), paper.group(2) or "", section)
    status, data, ctype = _download(url, 8_000_000)
    if status != 200 or not data:
        return f"fetch_url failed: {url} HTTP {status or 'error'}"
    if url.lower().split("?", 1)[0].endswith(".pdf") or "pdf" in ctype.lower() or data.startswith(b"%PDF"):
        return _format_pdf(url, data, section)
    text = _html_text(data.decode("utf-8", "replace"))
    if not text:
        return f"Source: {url}\nOpened {url}, but there was no readable text."
    return f"Source: {url}\n{text[:8000]}"


def _fetch_arxiv(paper_id: str, version: str, section: str) -> str:
    html_url = f"https://arxiv.org/html/{paper_id}{version}"
    status, data, _ctype = _download(html_url, 8_000_000)
    if status == 200 and data and b"<h" in data.lower():
        sections = _html_sections(data.decode("utf-8", "replace"))
        if sections:
            return _format_paper(html_url, sections, section)
    pdf_url = f"https://arxiv.org/pdf/{paper_id}{version}.pdf"
    status, data, _ctype = _download(pdf_url, 20_000_000)
    if status != 200 or not data.startswith(b"%PDF"):
        return f"Paper https://arxiv.org/abs/{paper_id}{version} was not read. The body was not extracted."
    return _format_pdf(pdf_url, data, section)


def _format_pdf(url: str, data: bytes, section: str) -> str:
    text = _pdf_text(data)
    if not text:
        return f"Paper {url} was not read. The body was not extracted."
    return _format_paper(url, _text_sections(text), section)


def _format_paper(url: str, sections: list[tuple[str, str]], section: str) -> str:
    names = [name for name, _body in sections if name]
    listing = "\n".join(f"- {name}" for name in names) or "- Body"
    wanted = section.strip()
    if wanted:
        folded = wanted.casefold()
        hits = [(name, body) for name, body in sections if folded in name.casefold()]
        if not hits:
            for name, body in sections:
                at = body.casefold().find(folded)
                if at >= 0:
                    start = max(0, at - 400)
                    excerpt = body[start : start + _PAPER_SECTION]
                    return f"Paper {url}\nNo heading matched {wanted}. Excerpt from {name}:\n\n{excerpt}"
            return f"Paper {url}\nNo section matching {wanted}.\nSections:\n{listing}"
        name, body = hits[0]
        return f"Paper {url}\nSection: {name}\n\n{body[:_PAPER_SECTION]}"
    first_name, first_body = sections[0]
    return (
        f"Paper {url}\nSections:\n{listing}\n\n"
        f"Section: {first_name}\n\n{first_body[:4000]}\n\n"
        "Call fetch_url again with section set to a heading to read that part."
    )


def _download(url: str, limit: int) -> tuple[int, bytes, str]:
    req = Request(url, headers={"User-Agent": "Termitaria/0.1 (research)"})
    try:
        with urlopen(req, timeout=40) as res:
            return res.status, res.read(limit), res.headers.get("Content-Type", "")
    except HTTPError as exc:
        return exc.code, exc.read(500), ""
    except (URLError, TimeoutError, OSError):
        return 0, b"", ""


def _html_sections(html: str) -> list[tuple[str, str]]:
    html = re.sub(r"(?is)<(script|style|nav|header|footer).*?>.*?</\1>", " ", html)
    parts = re.split(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", html)
    if len(parts) < 3:
        text = _html_text(html).strip()
        return [("Body", text[:_PAPER_SECTION])] if text else []
    sections: list[tuple[str, str]] = []
    preamble = _html_text(parts[0]).strip()
    if len(preamble) > 80:
        sections.append(("Preamble", preamble[:_PAPER_SECTION]))
    for index in range(1, len(parts), 2):
        title = _html_text(parts[index]).strip()[:200]
        body = _html_text(parts[index + 1]).strip() if index + 1 < len(parts) else ""
        if title and body:
            sections.append((title, body[:_PAPER_SECTION]))
    return sections


def _text_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = "Body"
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append((title, body[:_PAPER_SECTION]))

    for line in text.splitlines():
        stripped = line.strip()
        if _TEXT_HEADING.match(stripped) and len(buf) > 3:
            flush()
            buf = []
            title = stripped
            continue
        buf.append(line)
    flush()
    return sections or [("Body", text[:_PAPER_SECTION])]


def _pdf_text(data: bytes) -> str:
    if not data.startswith(b"%PDF") or not shutil.which("pdftotext"):
        return ""
    try:
        out = subprocess.run(
            ["pdftotext", "-q", "-layout", "-", "-"],
            input=data,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.decode("utf-8", "replace").strip()


def _html_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</p>", "\n", html)
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    return "\n".join(line.strip() for line in html.splitlines() if line.strip())


async def _ask_user(args: dict) -> str:
    known = str(args.get("known") or "").strip()
    question = str(args.get("question") or "").strip()
    if len(known) < 40 or len(question) < 20 or ("?" not in question and "？" not in question):
        return (
            "ask_user needs known (at least 40 characters of what you already have) "
            "and question (a specific question with a question mark)."
        )
    return f"What I already have:\n{known}\n\nQuestion:\n{question}"


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


def _write_report(gateway: str, project_id: str, deliverable: dict | None, sources: set | None) -> Runner:
    async def run(args: dict) -> str:
        title = str(args.get("title") or "Report").strip() or "Report"
        body = str(args.get("body") or "").strip()
        problem = report_error(body, deliverable, sources)
        if problem:
            return problem
        if not body.startswith("# "):
            body = f"# {title}\n\n{body}"
        name = _artifact_name(str(args.get("name") or ""), ".md", "report.md")
        evidence = sorted(collect_urls("\n".join(sources or [])))
        return await asyncio.to_thread(
            _store_artifact, gateway, project_id, name, "report", body.encode("utf-8"), evidence
        )

    return run


def report_error(body: str, deliverable: dict | None, sources: set | None) -> str:
    """成稿不合规格时返回给模型的那句原因。通过则返回空字符串。"""
    body = (body or "").strip()
    if not deliverable or not deliverable.get("sections"):
        if len(body) < 200:
            return "write_report needs the full report in body"
        return ""
    known = collect_urls("\n".join(sources or []))
    found = _section_bodies(body)
    for section in deliverable.get("sections") or []:
        heading = str(section.get("heading") or "").strip()
        text = found.get(heading.casefold())
        if text is None:
            return f"write_report is missing the section {heading}"
        if len(text) < int(section.get("min_chars") or 0):
            return f"write_report section {heading} is too short"
    for section in deliverable.get("sections") or []:
        if not section.get("citations"):
            continue
        heading = str(section.get("heading") or "").strip()
        if not _has_known_link(found.get(heading.casefold(), ""), known):
            return f"write_report section {heading} needs a link that a tool returned"
    if len(body) < int(deliverable.get("min_chars") or 0):
        return "write_report is too short"
    return ""


def collect_urls(text: str) -> set[str]:
    return {_clean_url(match) for match in _URL_RE.findall(text or "") if _clean_url(match)}


def _report_description(deliverable: dict | None) -> str:
    if not deliverable or not deliverable.get("sections"):
        return "Store the project report. body is markdown."
    lines = [
        "Store the project report. body is markdown with these ## headings.",
        "A section that asks for a link must include one URL a tool already returned.",
        "Other links and design numbers can stay in the text.",
    ]
    for section in deliverable.get("sections") or []:
        heading = section.get("heading") or ""
        minimum = int(section.get("min_chars") or 0)
        extra = " Include a link a tool returned." if section.get("citations") else ""
        lines.append(f"- ## {heading} (at least {minimum} characters).{extra}")
    if deliverable.get("require_deck"):
        lines.append("After this report is stored, call write_deck.")
    return "\n".join(lines)


def _section_bodies(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    current = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal current, buf
        if not current:
            buf = []
            return
        found[current] = "\n".join(buf).strip()
        buf = []

    for line in body.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            flush()
            current = match.group(1).strip().casefold()
            continue
        buf.append(line)
    flush()
    return found


def _clean_url(raw: str) -> str:
    return raw.strip().rstrip(".,);]>")


def _has_known_link(text: str, known: set[str]) -> bool:
    return any(link in known for link in collect_urls(text))


def _write_deck(gateway: str, project_id: str) -> Runner:
    async def run(args: dict) -> str:
        slides = _slides(args.get("slides"))
        if isinstance(slides, str):
            return slides
        title = str(args.get("title") or "Brief").strip() or "Brief"
        raw = build_pptx(title, slides)
        name = _artifact_name(str(args.get("name") or ""), ".pptx", "brief.pptx")
        return await asyncio.to_thread(_store_artifact, gateway, project_id, name, "deck", raw)

    return run


def _slides(raw) -> list[dict] | str:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return "write_deck slides must be a list of {title, bullets}"
    if not isinstance(raw, list) or len(raw) < 3:
        return "write_deck needs at least 3 slides"
    slides = []
    for item in raw:
        if isinstance(item, str):
            title, bullets = item[:80], [item]
        elif isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            bullets = item.get("bullets") or item.get("points") or []
            if isinstance(bullets, str):
                bullets = [bullets]
            bullets = [str(bullet).strip() for bullet in bullets if str(bullet).strip()]
        else:
            return "write_deck slides must be a list of {title, bullets}"
        if len(title) < 3 or not bullets:
            return "write_deck each slide needs a title and one bullet"
        slides.append({"title": title, "bullets": bullets})
    return slides


def _artifact_name(raw: str, suffix: str, fallback: str) -> str:
    name = Path(raw).name if raw else fallback
    if not name.endswith(suffix):
        stem = Path(name).stem or Path(fallback).stem
        name = stem + suffix
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", name):
        return fallback
    return name


def _store_artifact(gateway: str, project_id: str, name: str, kind: str, raw: bytes, evidence: list[str] | None = None) -> str:
    payload = {"name": name, "kind": kind, "content_base64": base64.standard_b64encode(raw).decode("ascii")}
    if evidence:
        payload["evidence_urls"] = evidence
    status, body = _post_json(
        f"{gateway}/api/projects/{project_id}/artifacts",
        payload,
        30,
    )
    if status != 201:
        return f"{kind} was not stored (HTTP {status}): {body[:300]}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = {}
    url = data.get("url") or f"/api/projects/{project_id}/artifacts/{name}"
    return f"Stored {kind} {data.get('name') or name} at {url}"


async def _edgar(args: dict) -> str:
    company = str(args.get("company") or args.get("name") or "").strip()
    if not company:
        return "edgar requires company"
    return await asyncio.to_thread(_edgar_blocking, company)


def _edgar_blocking(company: str) -> str:
    headers = {"User-Agent": "Termitaria/0.1 (local research; research@termitaria.local)", "Accept": "application/json"}
    try:
        tickers = json.loads(_sec_bytes("https://www.sec.gov/files/company_tickers.json", headers).decode("utf-8", "replace"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return f"edgar failed: {exc}"
    match = _match_filer(tickers, company)
    if match is None:
        return f"edgar found no US filer named {company}. Use web_search for companies outside EDGAR."
    cik, title, ticker = match
    padded = f"{cik:010d}"
    try:
        submissions = json.loads(_sec_bytes(f"https://data.sec.gov/submissions/CIK{padded}.json", headers).decode("utf-8", "replace"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return f"edgar failed: {exc}"
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    wanted = {"10-K", "10-Q", "8-K", "20-F", "6-K"}
    lines = [f"{title} ({ticker or 'no ticker'}) CIK {padded}. Open a filing with fetch_url."]
    kept = 0
    for index, form in enumerate(forms):
        if form not in wanted or index >= len(accessions) or index >= len(docs):
            continue
        accession = str(accessions[index])
        doc = str(docs[index])
        filed = dates[index] if index < len(dates) else ""
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"
        lines.append(f"- {form} filed {filed}\n  {url}")
        kept += 1
        if kept == 3:
            break
    if kept == 0:
        return f"edgar found {title} but no recent 10-K, 10-Q, or 8-K."
    return "\n".join(lines)


def _match_filer(tickers, company: str) -> tuple[int, str, str] | None:
    folded = company.casefold()
    rows = tickers.values() if isinstance(tickers, dict) else tickers
    fuzzy = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "")
        title = str(row.get("title") or "")
        try:
            cik = int(row.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if ticker.casefold() == folded:
            return cik, title, ticker
        if len(folded) >= 4 and folded in title.casefold() and fuzzy is None:
            fuzzy = (cik, title, ticker)
    return fuzzy


def _sec_bytes(url: str, headers: dict) -> bytes:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=20) as res:
        return res.read(2_000_000)
