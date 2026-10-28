"""工具协议和一轮工具循环。不连模型，不连 NATS。"""
import asyncio
from pathlib import Path

from services.worker import envelope
from services.worker.graph import build_graph
from services.worker.model import Chunk, Done
from services.worker.runner import TaskRunner
from services.worker.skills import load_skills
from services.worker.rules import load_rules
from services.worker.tools import Toolbox, Tool, mention_delegates, parse_tool_calls
from termitaria.task.v1 import task_pb2

from test_worker_offline import FakeNC, make_task


def test_reply_follows_the_user_language():
    rules = load_rules(Path("rules"))
    assert "Reply in the user's language." in rules

    async def _noop(_arguments):
        return ""

    text = Toolbox([Tool("web_search", "search", _noop)]).instructions()
    assert "English" not in text
    assert "language" not in text


def test_load_skill_keeps_name_argument():
    calls = parse_tool_calls('{"tool":"load_skill","name":"filings"}')
    assert calls[0].name == "load_skill"
    assert calls[0].arguments["name"] == "filings"


def test_adapter_reads_provider_shapes():
    from services.worker.calls import extract_tool_call

    openai = extract_tool_call({
        "id": "call_1",
        "function": {"name": "web_search", "arguments": "{\"query\":\"tsla\"}"},
    })
    assert openai is not None
    assert openai.name == "web_search"
    assert openai.arguments == {"query": "tsla"}
    anthropic = extract_tool_call({"id": "tu_1", "name": "fetch_url", "input": {"url": "https://x"}})
    assert anthropic is not None
    assert anthropic.arguments == {"url": "https://x"}


def test_think_block_is_not_a_tool_call():
    from services.worker.tools import visible_text

    raw = '<think>先试试 {"tool":"web_search","query":"tesla pdf"}</think>'
    assert visible_text(raw) == ""
    assert parse_tool_calls(visible_text(raw)) == []
    cut = '<think>预算不够 {"tool":"fetch_url","url":"https://example.com"}'
    assert "fetch_url" not in visible_text(cut)
    kept = visible_text('<think>推理</think>\n{"tool":"fetch_url","url":"https://example.com/a.pdf"}')
    assert parse_tool_calls(kept)[0].name == "fetch_url"
    # DeepSeek 有时只给闭合标签，前面整段是推理。这是 ChatNVIDIA 的第二种格式。
    bare = visible_text('先搜 {"tool":"web_search","query":"x"}</think>\n摘要在这里')
    assert bare == "摘要在这里"
    assert parse_tool_calls(bare) == []


def test_parse_flat_and_nested_calls():
    text = '\n'.join([
        '{"tool":"delegate","coworker":"quotes","task":"查收盘价","context":"上周"}',
        '{"tool":"write_file","arguments":{"path":"tsla_report.md","content":"hi"}}',
    ])
    calls = parse_tool_calls(text)
    assert calls[0].name == "delegate"
    assert calls[0].arguments["coworker"] == "quotes"
    assert calls[1].name == "write_file"
    assert calls[1].arguments["path"] == "tsla_report.md"
    assert parse_tool_calls("这是给用户的话，没有工具。") == []


def test_mention_becomes_delegate():
    calls = mention_delegates(
        "交给 @quotes 和 @Market Quotes 之外的 @scribe",
        [
            {"id": "quotes", "display_name": "Market Quotes"},
            {"id": "scribe", "display_name": "Report Scribe"},
        ],
    )
    assert [call.arguments["coworker"] for call in calls] == ["quotes", "scribe"]


def test_skill_frontmatter(tmp_path: Path):
    (tmp_path / "market-data.md").write_text(
        "---\nname: market-data\ndescription: 查收盘价\n---\n打开 Yahoo。\n",
        encoding="utf-8",
    )
    skills = load_skills(tmp_path)
    assert skills[0].name == "market-data"
    assert skills[0].body == "打开 Yahoo。"


def test_utc_time_converts_both_ways():
    from services.worker.tools import _utc_time

    text = asyncio.run(_utc_time({"date": "2026-10-12"}))
    assert text == "2026-10-12 00:00 UTC = 1791763200"
    back = asyncio.run(_utc_time({"unix": 1791763200}))
    assert back.startswith("1791763200 = 2026-10-12")


def test_write_file_stays_in_workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:9")

    class Scripted:
        def __init__(self):
            self.round = 0

        async def stream(self, **kwargs):
            self.round += 1
            if self.round == 1:
                yield Chunk('{"tool":"write_file","path":"out.md","content":"hello"}')
            else:
                yield Chunk("写好了")
            yield Done(task_pb2.TaskUsage(model="scripted"))

    nc = FakeNC()
    runner = TaskRunner(nc, Scripted(), producer="test")
    result = asyncio.run(runner.run(make_task(), "trace"))
    assert result.status == task_pb2.TASK_STATUS_COMPLETED
    assert result.message.content[0].text == "写好了"
    assert (tmp_path / "workspace" / "sw1" / "out.md").read_text(encoding="utf-8") == "hello"
    streamed = []
    for subject, data in nc.published:
        if ".delta." not in subject:
            continue
        delta = envelope.unpack(data, task_pb2.TaskDelta)[1]
        streamed.append(delta.text)
    assert "写好了" in "".join(streamed)
    assert "write_file" not in "".join(streamed)


def test_read_file_stays_in_the_project_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    workspace = tmp_path / "workspace" / "p"
    workspace.mkdir(parents=True)
    (workspace / "protocol.md").write_text("design of record", encoding="utf-8")
    (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
    (workspace / "var" / "memory").mkdir(parents=True)
    (workspace / "var" / "memory" / "episodes.jsonl").write_text("another agent's notes", encoding="utf-8")
    from services.worker.tools import _read_file

    read = _read_file("p")
    assert asyncio.run(read({"path": "protocol.md"})) == "design of record"
    assert asyncio.run(read({"path": "../outside.md"})).startswith("Refused to read")
    assert asyncio.run(read({"path": "missing.md"})).startswith("No file at")
    assert asyncio.run(read({"path": ".env"})).startswith("Refused to read hidden")
    assert asyncio.run(read({"path": "var/memory/episodes.jsonl"})).startswith("Refused to read")


def test_read_file_points_uploads_to_kg(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    from services.worker.tools import _read_file

    read = _read_file("p", [{"id": "u_1", "kind": "file", "title": "uploaded.md", "body": "from kg"}])
    out = asyncio.run(read({"path": "uploaded.md"}))
    assert "uploaded project file" in out
    assert "kg action=read" in out


def test_kg_read_returns_full_project_material(monkeypatch):
    from services.worker.tools import _kg

    body = "x" * 20000
    monkeypatch.setattr(
        "services.worker.tools.load_project",
        lambda gateway, project_id: {
            "seeds": [{"id": "u_1", "kind": "file", "title": "protocol.md", "body": body}]
        },
    )
    out = asyncio.run(_kg("p", "http://gateway")({"action": "read", "title": "protocol.md"}))
    assert "Project material: protocol.md" in out
    assert body in out
    missing = asyncio.run(_kg("p", "http://gateway")({"action": "read", "title": "missing.md"}))
    assert "No project upload" in missing
    assert "read_file" in missing


def test_write_file_is_stored_as_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(
        "services.worker.tools._store_artifact",
        lambda gateway, project_id, name, kind, raw: calls.append((gateway, project_id, name, kind, raw))
        or "Stored file protocol.md at /x",
    )
    from services.worker.tools import _write_file

    out = asyncio.run(_write_file("http://gateway", "p")({"path": "protocol.md", "content": "hello"}))
    assert (tmp_path / "workspace" / "p" / "protocol.md").read_text(encoding="utf-8") == "hello"
    assert calls == [("http://gateway", "p", "protocol.md", "file", b"hello")]
    assert "Stored file" in out


def test_closing_round_runs_text_calls_then_asks_for_prose(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_DIR", str(tmp_path))
    monkeypatch.setenv("GATEWAY_URL", "http://127.0.0.1:9")

    class Scripted:
        def __init__(self):
            self.bare = 0

        async def stream(self, **kwargs):
            if kwargs.get("tools"):
                yield Chunk('{"tool":"write_file","path":"a.md","content":"x"}')
            else:
                self.bare += 1
                if self.bare == 1:
                    yield Chunk('{"tool":"write_file","path":"b.md","content":"y"}')
                else:
                    yield Chunk("两份都写好了。")
            yield Done(task_pb2.TaskUsage(model="scripted"))

    nc = FakeNC()
    runner = TaskRunner(nc, Scripted(), producer="test")
    result = asyncio.run(runner.run(make_task(), "trace"))
    body = result.message.content[0].text
    assert result.status == task_pb2.TASK_STATUS_COMPLETED
    assert body == "两份都写好了。"
    assert (tmp_path / "workspace" / "sw1" / "b.md").read_text(encoding="utf-8") == "y"
    streamed = []
    for subject, data in nc.published:
        if ".delta." not in subject:
            continue
        delta = envelope.unpack(data, task_pb2.TaskDelta)[1]
        streamed.append(delta.text)
    assert "write_file" not in "".join(streamed)


def test_search_reports_missing_key(monkeypatch):
    monkeypatch.setattr("services.worker.tools._search_key", lambda: "")
    from services.worker.tools import _search_blocking

    assert "SEARCH_API_KEY" in _search_blocking("copper")


def test_filings_skill_adds_edgar():
    from services.worker.skills import Skill
    from services.worker.tools import make_toolbox

    market = make_toolbox(
        agent={"id": "researcher", "kind": "WORKER", "tools": ["web_search"], "skills": ["market-data"]},
        project_id="p",
        steps=[],
        skills=[Skill("market-data", "prices", "yahoo")],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
    )
    filings = make_toolbox(
        agent={"id": "researcher", "kind": "WORKER", "tools": ["web_search"], "skills": ["filings"]},
        project_id="p",
        steps=[],
        skills=[Skill("filings", "pdf", "call edgar")],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
    )
    assert "load_skill" in market.names()
    assert "edgar" not in market.names()
    assert "edgar" in filings.names()


def test_ask_user_rejects_a_vague_question():
    from services.worker.tools import _ask_user

    text = asyncio.run(_ask_user({"known": "short", "question": "huh"}))
    assert text.startswith("ask_user needs")


def test_deck_is_a_pptx():
    import io
    import zipfile

    from services.worker.deck import build_pptx

    raw = build_pptx("Copper", [
        {"title": "Move", "bullets": ["COMEX fell"]},
        {"title": "Why", "bullets": ["No sourced cause"]},
        {"title": "Watch", "bullets": ["LME stocks"]},
    ])
    assert len(raw) >= 800
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        xml = package.read("ppt/slides/slide1.xml").decode()
    assert "Move" in xml


def test_orchestrator_asks_instead_of_stopping(monkeypatch):
    monkeypatch.setattr(
        "services.worker.graph.load_project",
        lambda gateway, project_id: {
            "id": "p1",
            "steps": [{"id": "s1", "label": "Price the week", "state": "active"}],
            "agents": [
                {"id": "pi", "kind": "ORCHESTRATOR", "tools": ["ledger"], "skills": []},
                {
                    "id": "researcher",
                    "display_name": "Market Researcher",
                    "kind": "WORKER",
                    "skills": ["market-data"],
                    "tools": ["web_search"],
                },
            ],
            "seeds": [{"kind": "text", "title": "LME", "body": "LME copper closed at 9840."}],
        },
    )

    class Scripted:
        def __init__(self):
            self.round = 0

        async def stream(self, **kwargs):
            self.round += 1
            if self.round == 1:
                yield Chunk("I will stop here.")
            else:
                yield Chunk(
                    '{"tool":"ask_user","question":"Which producer should the filings cover?",'
                    '"known":"COMEX copper fell about 2.5 percent last week on the Yahoo chart, and the seed note quotes LME at 9840."}'
                )
            yield Done(task_pb2.TaskUsage(model="scripted"))

    task = make_task()
    task.agent_id = "pi"
    task.swarm_id = "p1"
    result = asyncio.run(TaskRunner(FakeNC(), Scripted(), producer="test").run(task, "trace"))
    body = result.message.content[0].text
    assert "Which producer" in body
    assert "I will stop here." not in body


def test_workers_get_default_tools_without_spec():
    from services.worker.tools import make_toolbox

    worker = make_toolbox(
        agent={"id": "ideator", "kind": "WORKER", "tools": ["kg_query"]},
        project_id="p",
        steps=[],
        skills=[],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
    )
    for name in ("web_search", "fetch_url", "utc_time", "write_file", "read_file"):
        assert name in worker.names()
    assert "delegate" not in worker.names()
    assert "advance" not in worker.names()
    assert "write_report" not in worker.names()

    lead = make_toolbox(
        agent={"id": "pi", "kind": "ORCHESTRATOR", "tools": ["ledger"]},
        project_id="p",
        steps=[],
        skills=[],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
    )
    assert "web_search" not in lead.names()
    assert "read_file" in lead.names()
    assert "delegate" in lead.names()
    assert "write_report" in lead.names()


def test_orchestrator_without_coworkers_has_no_delegate():
    from services.worker.tools import make_toolbox

    lead = make_toolbox(
        agent={"id": "lead", "kind": "ORCHESTRATOR", "tools": ["ledger"]},
        project_id="p",
        steps=[],
        skills=[],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
        can_delegate=False,
    )
    assert "delegate" not in lead.names()
    assert "advance" in lead.names()


def test_report_requires_sections_and_tool_links():
    from services.worker.tools import report_error

    deliverable = {
        "kind": "brief",
        "min_chars": 40,
        "require_deck": True,
        "sections": [
            {"heading": "Price table", "min_chars": 20, "citations": True},
            {"heading": "Why", "min_chars": 20, "citations": True},
        ],
    }
    urls = {"https://example.com/copper"}
    missing = "## Price table\n\nMonday close was 4.52. https://example.com/copper\n"
    assert "Why" in report_error(missing, deliverable, urls)
    unlinked = (
        "## Price table\n\nMonday close was 4.52.\n\n"
        "## Why\n\nStocks fell at the warehouse and stayed low.\n"
    )
    assert "link" in report_error(unlinked, deliverable, urls)
    invented = (
        "## Price table\n\nMonday close was 4.52. https://evil.example/x\n\n"
        "## Why\n\nStocks fell at the warehouse. https://evil.example/x\n"
    )
    assert "link" in report_error(invented, deliverable, urls)
    good = (
        "## Price table\n\nMonday close was 4.52. https://example.com/copper\n\n"
        "## Why\n\nThe glossary is 200 tokens.\n\n"
        "Stocks fell at the warehouse. https://example.com/copper https://evil.example/x\n"
    )
    assert report_error(good, deliverable, urls) == ""


def test_only_the_lead_delegates_and_it_runs_here():
    from services.worker.tools import make_toolbox

    blocked = make_toolbox(
        agent={"id": "ideator", "kind": "WORKER", "tools": ["delegate", "kg_query"]},
        project_id="p",
        steps=[],
        skills=[],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
    )
    assert "delegate" not in blocked.names()

    seen = {}

    async def run(coworker, task, context):
        seen["coworker"] = coworker
        seen["task"] = task
        seen["context"] = context
        return "ideator replied:\nH1 holds."

    lead = make_toolbox(
        agent={"id": "pi", "kind": "ORCHESTRATOR", "tools": ["ledger"]},
        project_id="p",
        steps=[],
        skills=[],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
        seeds=[{"kind": "text", "title": "Question", "body": "GPQA"}],
        delegate_run=run,
    )
    text = asyncio.run(lead.run("delegate", {"coworker": "ideator", "task": "Draft H1", "context": "gap"}))
    assert seen["coworker"] == "ideator"
    assert seen["task"] == "Draft H1"
    assert "GPQA" in seen["context"]
    assert text == "ideator replied:\nH1 holds."


def test_running_turn_reads_a_file_uploaded_midway(monkeypatch):
    import asyncio

    from services.worker import graph

    seen = {"n": 0}

    def load_project(gateway, project_id):
        del gateway
        seen["n"] += 1
        seeds = []
        if seen["n"] > 1:
            seeds.append({"id": "u_new", "title": "notes.md", "body": "Copper closed at 9840."})
        return {"id": project_id, "seeds": seeds}

    async def knowledge(project_id, timeout_s=8):
        del project_id, timeout_s
        return "Knowledge graph:\n- concept: Copper — priced at 9840\n\nRelations:\n- PRICED_AT: Copper -> 9840"

    monkeypatch.setattr(graph, "load_project", load_project)
    monkeypatch.setattr(graph, "_project_knowledge", knowledge)
    bag = graph._upload_watch("http://gw", "p1", [])

    async def run():
        before = await graph._note_uploads(bag)
        during = await graph._note_uploads(bag)
        return before, during

    before, during = asyncio.run(run())
    assert before == ""
    assert "notes.md" in during
    assert "Copper closed at 9840." in during
    assert "PRICED_AT: Copper -> 9840" in during


def test_fresh_uploads_are_the_files_added_during_a_task():
    from services.worker.tools import fresh_uploads, upload_notice, uploaded_files

    seen = {"k_old"}
    first = fresh_uploads(
        [
            {"id": "k_old", "title": "Old", "body": "already in the prompt"},
            {"kind": "text", "title": "Seed", "body": "no id"},
            {"id": "u_new", "title": "notes.md", "body": "Copper closed at 9840."},
        ],
        seen,
    )
    assert [item["id"] for item in first] == ["u_new"]
    assert "u_new" in seen
    note = upload_notice(first)
    assert "notes.md" in note
    assert "Copper closed at 9840." in note
    assert "action query" in note
    assert fresh_uploads([{"id": "u_new", "title": "notes.md", "body": "Copper closed at 9840."}], seen) == []
    listed = uploaded_files({"seeds": [{"id": "u_new", "title": "notes.md", "body": "Copper closed at 9840."}]})
    assert "notes.md" in listed
    assert "Copper closed at 9840." in listed
    assert uploaded_files({"seeds": [{"id": "k_old", "title": "Old", "body": "seed"}]}) == ""


def test_kg_tool_queues_a_note_and_reads_relations():
    from services.graph.graphiti_kg import node_text
    from services.worker.tools import _kg_read, _kg_write
    from termitaria.graph.v1 import graph_pb2

    class Fake:
        def __init__(self):
            self.nodes = {}
            self.edges = {}

        async def upsert_node(self, node):
            self.nodes[node.node_id] = node
            return node.node_id

        async def upsert_edge(self, edge):
            self.edges[edge.edge_id] = edge
            return edge.edge_id

        async def query_subgraph(self, seeds, depth=1, edge_kinds=None, max_nodes=32, strict=False):
            del seeds, depth, edge_kinds, max_nodes, strict
            resp = graph_pb2.QuerySubgraphResponse()
            resp.nodes.extend(self.nodes.values())
            resp.edges.extend(self.edges.values())
            return resp

    async def run() -> None:
        client = Fake()
        stored = await _kg_write(
            client,
            "p1",
            {"title": "Radar carrier", "text": "The carrier is 77 GHz.", "kind": "claim"},
        )
        assert stored.startswith("Queued this text")
        assert any("77 GHz" in node_text(node) for node in client.nodes.values())
        assert client.edges == {}
        await client.upsert_node(graph_pb2.Node(node_id="n1", kind=graph_pb2.NODE_KIND_CONCEPT, title="Northwire"))
        await client.upsert_node(graph_pb2.Node(node_id="n2", kind=graph_pb2.NODE_KIND_CONCEPT, title="Houston quote"))
        await client.upsert_edge(
            graph_pb2.Edge(
                edge_id="e1",
                from_node_id="n1",
                to_node_id="n2",
                relation="PRICED_AT",
                fact="The brief prices the wire at 4.80.",
            )
        )
        text = await _kg_read(client, "p1")
        assert "77 GHz" in text
        assert "PRICED_AT: Northwire -> Houston quote — The brief prices the wire at 4.80." in text
        assert "CITES" not in text

    asyncio.run(run())


def test_paper_fetch_returns_one_section(monkeypatch):
    from services.worker.tools import _fetch_blocking

    html = (
        "<html><body><nav>Skip to main content</nav>"
        "<h2>Abstract</h2><p>GPQA has 448 questions.</p>"
        "<h2>Datasets</h2><p>Diamond has 198 items.</p></body></html>"
    ).encode()

    def fake_download(url, limit):
        del limit
        if "/html/" in url:
            return 200, html, "text/html"
        return 404, b"", ""

    monkeypatch.setattr("services.worker.tools._download", fake_download)
    text = _fetch_blocking("https://arxiv.org/abs/2311.12022", "Datasets")
    assert "Diamond has 198 items." in text
    assert "Skip to main content" not in text
    assert "448 questions" not in text


def test_unread_pdf_does_not_invent_citation_keys(monkeypatch):
    from services.worker.tools import _fetch_blocking

    monkeypatch.setattr("services.worker.tools._download", lambda url, limit: (200, b"%PDF-1.4 (cite.gpqa) cover", "application/pdf"))
    monkeypatch.setattr("services.worker.tools._pdf_text", lambda data: "")
    text = _fetch_blocking("https://example.com/paper.pdf")
    assert "was not read" in text
    assert "cite.gpqa" not in text


def test_handoff_keeps_real_tools_and_drops_narrated_ones():
    from services.worker.graph import coworker_handoff

    text = coworker_handoff(
        "The gap is open.\ntool: advance → Hypothesis marked done.\ntool: write_file → notes-litscan.md written.",
        ["fetch_url → Paper https://arxiv.org/abs/2311.12022\n448 questions"],
    )
    spoken, _, rest = text.partition("Tools that ran:")
    assert "Hypothesis marked done" not in spoken
    assert "notes-litscan.md" not in spoken
    assert "fetch_url → Paper" in rest
    assert "Steps changed:\nnone" in text
    assert "Files written:\nnone" in text


def test_graph_builds():
    # 编译一次，确认工具循环没有把图接错。
    build_graph(object(), lambda delta: _noop(delta))


async def _noop(_delta):
    return None
