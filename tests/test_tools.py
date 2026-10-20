"""工具协议和一轮工具循环。不连模型，不连 NATS。"""
import asyncio
from pathlib import Path

from services.worker import envelope
from services.worker.graph import build_graph
from services.worker.model import Chunk, Done
from services.worker.runner import TaskRunner
from services.worker.skills import load_skills
from services.worker.tools import mention_delegates, parse_tool_calls
from termitaria.task.v1 import task_pb2

from test_worker_offline import FakeNC, make_task


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
    assert (tmp_path / "out.md").read_text(encoding="utf-8") == "hello"
    streamed = []
    for subject, data in nc.published:
        if ".delta." not in subject:
            continue
        delta = envelope.unpack(data, task_pb2.TaskDelta)[1]
        streamed.append(delta.text)
    assert "写好了" in "".join(streamed)
    assert "write_file" not in "".join(streamed)


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
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "y"
    streamed = []
    for subject, data in nc.published:
        if ".delta." not in subject:
            continue
        delta = envelope.unpack(data, task_pb2.TaskDelta)[1]
        streamed.append(delta.text)
    assert "write_file" not in "".join(streamed)


def test_graph_builds():
    # 编译一次，确认工具循环没有把图接错。
    build_graph(object(), lambda delta: _noop(delta))


async def _noop(_delta):
    return None
