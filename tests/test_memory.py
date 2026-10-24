"""离线检查：后端开关、MemoryItem 映射、distiller 发布、召回降级。"""
from __future__ import annotations

import asyncio

# gen/python 在 services 包导入时才放进 sys.path。
import services
from termitaria.memory.v1 import memory_pb2
from termitaria.session.v1 import session_pb2

from services.common import envelope
from services.memory.backend import CannedBackend, build_backend
from services.memory.canned import build_recall_response
from services.memory.config import MemoryConfig
from services.memory.distiller import Distiller
from services.memory.mem0_backend import add_messages
from services.memory.graph_view import build_graph_view, empty_graph_view
from services.memory.items import assemble_response, graphiti_items, mem0_items, principle_id
from services.memory.server import RecallResponder

_ENV_KEYS = (
    "MEMORY_BACKEND",
    "MEMORY_LLM_API_KEY",
    "MEMORY_LLM_BASE_URL",
    "MEMORY_LLM_MODEL",
    "MEMORY_EMBED_MODEL",
    "NEBIUS_API_KEY",
    "NEBIUS_BASE_URL",
    "OPENAI_API_KEY",
    "QDRANT_HOST",
    "QDRANT_PORT",
    "QDRANT_COLLECTION",
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "NATS_URL",
    "MEMORY_SPOOL_DIR",
)


def _clear_env(monkeypatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_backend_defaults_to_canned(monkeypatch):
    _clear_env(monkeypatch)
    cfg = MemoryConfig.from_env()
    assert cfg.backend == "canned"
    assert cfg.llm_api_key == ""
    assert cfg.llm_base_url == "https://api.openai.com/v1"
    assert cfg.embed_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert cfg.qdrant_host == "localhost"
    assert cfg.qdrant_port == 6333
    assert cfg.qdrant_collection == "termitaria_mem0"
    assert cfg.falkordb_host == "localhost"
    assert cfg.falkordb_port == 6379


def test_backend_env_selects_graphiti_and_falls_back_for_the_key(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MEMORY_BACKEND", " Graphiti ")
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-key")
    monkeypatch.setenv("MEMORY_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("MEMORY_LLM_MODEL", "demo-model")
    monkeypatch.setenv("FALKORDB_PORT", "6380")
    cfg = MemoryConfig.from_env()
    assert cfg.backend == "graphiti"
    assert cfg.llm_api_key == "nebius-key"
    assert cfg.llm_base_url == "https://llm.example/v1"
    assert cfg.llm_model == "demo-model"
    assert cfg.falkordb_port == 6380


def test_canned_recall_does_not_invent_an_empty_episode():
    resp = build_recall_response(memory_pb2.RecallRequest(agent_id="pi", top_k=5))
    assert resp.items
    assert all(item.kind != memory_pb2.MEMORY_KIND_EPISODE for item in resp.items)


def test_mem0_without_credentials_stays_on_canned():
    cfg = MemoryConfig(nats_url="nats://localhost:4222", backend="mem0")
    backend = asyncio.run(build_backend(cfg))
    assert isinstance(backend, CannedBackend)
    assert backend.degraded is True


def test_mem0_hit_maps_to_episode_under_the_rule():
    hits = [
        {
            "id": "m1",
            "memory": "Ada likes tea",
            "score": 0.8,
            "run_id": "s1",
            "metadata": {"occurred_at_ms": 50},
        }
    ]
    req = memory_pb2.RecallRequest(
        agent_id="pi",
        depth=memory_pb2.RECALL_DEPTH_EPISODES_ONLY,
        top_k=5,
    )
    resp = assemble_response(req, mem0_items("pi", hits))
    assert resp.meta.degraded is False
    assert resp.meta.candidate_count == 1
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.kind == memory_pb2.MEMORY_KIND_EPISODE
    assert item.source_session_id == "s1"
    assert item.score == 0.8
    assert item.occurred_at_ms == 50
    assert item.text == "Ada likes tea"


def test_graphiti_nodes_and_edges_keep_kind_and_depth_filter():
    nodes = [{"uuid": "n1", "summary": "Tea is a drink", "name": "Tea"}]
    edges = [
        {
            "uuid": "e1",
            "fact": "Ada likes tea",
            "source_node_uuid": "n1",
            "valid_at": 1_700_000_000_000,
            "score": 0.4,
        }
    ]
    items = graphiti_items("pi", nodes, edges)
    auto = assemble_response(
        memory_pb2.RecallRequest(agent_id="pi", depth=memory_pb2.RECALL_DEPTH_AUTO, top_k=10),
        items,
    )
    by_kind = {it.kind: it for it in auto.items}
    principle = by_kind[memory_pb2.MEMORY_KIND_PRINCIPLE]
    episode = by_kind[memory_pb2.MEMORY_KIND_EPISODE]
    assert principle.item_id == principle_id("pi", "n1")
    assert episode.occurred_at_ms == 1_700_000_000_000
    assert memory_pb2.MEMORY_KIND_ROLE in by_kind
    assert memory_pb2.MEMORY_KIND_RULE in by_kind

    principles = assemble_response(
        memory_pb2.RecallRequest(
            agent_id="pi",
            depth=memory_pb2.RECALL_DEPTH_PRINCIPLES_ONLY,
            top_k=10,
        ),
        items,
    )
    assert all(it.kind != memory_pb2.MEMORY_KIND_EPISODE for it in principles.items)
    assert principles.meta.candidate_count == 3
    assert principles.meta.reranked_count == 3


class _JS:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, subject, data, **kwargs):
        del kwargs
        self.published.append((subject, data))


class _NC:
    def __init__(self, js: _JS) -> None:
        self._js = js

    def jetstream(self) -> _JS:
        return self._js


class _Msg:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


def _event(ev: session_pb2.SessionEvent) -> _Msg:
    return _Msg(envelope.pack("test", "trace-1", ev))


def _user_line(text: str) -> session_pb2.Message:
    message = session_pb2.Message(content=[session_pb2.ContentBlock(text=text)])
    getattr(message, "from").user_id = "u1"
    return message


def _agent_line(text: str) -> session_pb2.Message:
    message = session_pb2.Message(content=[session_pb2.ContentBlock(text=text)])
    getattr(message, "from").agent_id = "ideator"
    return message


def test_distiller_writes_the_user_sentence_for_each_agent():
    async def run() -> None:
        js = _JS()
        distiller = Distiller(_NC(js), MemoryConfig(nats_url="nats://localhost:4222"))
        opened = session_pb2.SessionEvent(
            session_id="s1",
            swarm_id="sw1",
            opened=session_pb2.SessionOpened(
                session=session_pb2.Session(
                    participants=[
                        session_pb2.Participant(agent_id="ideator"),
                        session_pb2.Participant(agent_id="reviewer"),
                    ]
                )
            ),
        )
        user_line = session_pb2.SessionEvent(
            session_id="s1",
            swarm_id="sw1",
            message_appended=session_pb2.MessageAppended(message=_user_line("alpha")),
        )
        agent_line = session_pb2.SessionEvent(
            session_id="s1",
            swarm_id="sw1",
            message_appended=session_pb2.MessageAppended(message=_agent_line("beta")),
        )
        closed = session_pb2.SessionEvent(
            session_id="s1",
            swarm_id="sw1",
            closed=session_pb2.SessionClosed(reason="done"),
        )
        await distiller._handle(_event(opened))
        await distiller._handle(_event(user_line))
        assert distiller.fanned_out == [("s1", "ideator"), ("s1", "reviewer")]
        await distiller._handle(_event(agent_line))
        await distiller._handle(_event(closed))

        assert [subject for subject, _ in js.published] == [
            "memory.write.document",
            "memory.write.episode",
            "memory.write.episode",
        ]
        episodes = []
        for subject, data in js.published:
            if subject != "memory.write.episode":
                continue
            _, req = envelope.unpack(data, memory_pb2.WriteEpisodeRequest)
            episodes.append(req.episode)
        assert {ep.agent_id for ep in episodes} == {"ideator", "reviewer"}
        assert episodes[0].session_id == "s1"
        assert episodes[0].swarm_id == "sw1"
        assert episodes[0].summary == "alpha"
        assert list(episodes[0].salient_spans) == ["alpha"]

    asyncio.run(run())


def test_distiller_restores_open_sessions_without_rewriting_old_lines():
    async def run() -> None:
        js = _JS()
        distiller = Distiller(_NC(js), MemoryConfig(nats_url="nats://localhost:4222"))
        opened = session_pb2.SessionEvent(
            session_id="s1",
            swarm_id="sw1",
            opened=session_pb2.SessionOpened(
                session=session_pb2.Session(
                    participants=[session_pb2.Participant(agent_id="pi")]
                )
            ),
        )
        old_line = session_pb2.SessionEvent(
            session_id="s1",
            swarm_id="sw1",
            message_appended=session_pb2.MessageAppended(message=_user_line("butter")),
        )
        closed = session_pb2.SessionEvent(
            session_id="s2",
            swarm_id="sw1",
            closed=session_pb2.SessionClosed(reason="done"),
        )
        distiller._apply_roster(opened)
        distiller._remember_roster(_event(old_line))
        distiller._apply_roster(
            session_pb2.SessionEvent(
                session_id="s2",
                swarm_id="sw1",
                opened=session_pb2.SessionOpened(
                    session=session_pb2.Session(
                        participants=[session_pb2.Participant(agent_id="reviewer")]
                    )
                ),
            )
        )
        distiller._apply_roster(closed)
        assert js.published == []
        assert distiller._participants == {"s1": ["pi"]}

        fresh = session_pb2.SessionEvent(
            session_id="s1",
            swarm_id="sw1",
            message_appended=session_pb2.MessageAppended(message=_user_line("hot chocolate")),
        )
        await distiller._handle(_event(fresh))
        assert distiller.fanned_out == [("s1", "pi")]
        _, doc = envelope.unpack(js.published[0][1], memory_pb2.WriteDocumentRequest)
        assert doc.document.doc_id.startswith("sw1/")
        _, req = envelope.unpack(js.published[1][1], memory_pb2.WriteEpisodeRequest)
        assert req.episode.summary == "hot chocolate"
        assert req.episode.swarm_id == "sw1"
        await distiller._handle(
            _event(
                session_pb2.SessionEvent(
                    session_id="s1",
                    swarm_id="sw1",
                    closed=session_pb2.SessionClosed(reason="done"),
                )
            )
        )
        assert len(js.published) == 2

    asyncio.run(run())


def test_graph_view_keeps_one_fact_and_one_expired_fact():
    view = build_graph_view(
        "graphiti",
        "pi",
        [
            {"id": "a", "name": "User", "summary": "The person in the chat."},
            {"id": "b", "name": "MILK", "summary": "A drink."},
            {"id": "a", "name": "duplicate", "summary": "ignored"},
        ],
        [
            {
                "id": "e1",
                "source": "a",
                "target": "b",
                "fact": "favourite drink is MILK",
                "name": "LIKES",
                "valid_at": "2026-10-21",
                "invalid_at": "",
            },
            {
                "id": "e2",
                "source": "a",
                "target": "b",
                "fact": "favourite drink is hot chocolate",
                "name": "LIKES",
                "valid_at": "2026-10-21",
                "invalid_at": "2026-10-22",
            },
            {"id": "e3", "source": "missing", "target": "b", "fact": "dropped"},
        ],
    )
    assert view["backend"] == "graphiti"
    assert view["agent_id"] == "pi"
    assert [node["id"] for node in view["nodes"]] == ["a", "b"]
    assert view["edges"][0]["expired"] is False
    assert view["edges"][1]["expired"] is True
    assert view["edges"][1]["fact"] == "favourite drink is hot chocolate"
    assert len(view["edges"]) == 2
    capped = build_graph_view(
        "graphiti",
        "pi",
        [{"id": f"n{i}", "name": f"n{i}", "summary": ""} for i in range(81)],
        [],
    )
    assert len(capped["nodes"]) == 80

    empty = empty_graph_view("mem0", "pi")
    assert empty == {"backend": "mem0", "agent_id": "pi", "nodes": [], "edges": []}


def test_mem0_add_keeps_the_user_sentence():
    messages, kwargs = add_messages(
        "实验室编号是 17-BLUE。",
        agent_id="pi",
        run_id="s1",
        metadata={"occurred_at_ms": 1, "run_id": "s1"},
    )
    assert messages == [{"role": "user", "content": "实验室编号是 17-BLUE。"}]
    assert kwargs["infer"] is False
    assert kwargs["agent_id"] == "pi"
    assert kwargs["run_id"] == "s1"

    _, document = add_messages("notes", agent_id="documents", metadata={"doc_id": "d1"})
    assert document["infer"] is False
    assert "run_id" not in document


def test_warm_queries_embed_once_then_time_three_recalls(tmp_path):
    from services.memory.timing import publish_recall_wait, recommend_recall_wait_ms, time_warm_queries

    async def run() -> None:
        calls = []

        async def embed(text: str) -> None:
            calls.append(("embed", text))

        async def recall() -> int:
            calls.append("recall")
            return 400

        samples = await time_warm_queries(embed, recall)
        assert calls == [("embed", "warmup"), "recall", "recall", "recall"]
        assert samples == [400, 400, 400]
        assert recommend_recall_wait_ms(samples) == 1200
        assert recommend_recall_wait_ms([]) == 0
        assert recommend_recall_wait_ms([2000, 1000]) == 3000

        class Backend:
            name = "mem0"

            async def warmup(self) -> list[int]:
                return [200, 400]

        path = tmp_path / "recall-wait-ms"
        wait = await publish_recall_wait(Backend(), path)
        assert wait == 1200
        assert path.read_text() == "1200\n"

    asyncio.run(run())


def test_recall_degrades_when_the_backend_raises():
    async def run() -> None:
        class Boom:
            async def recall(self, req):
                del req
                raise RuntimeError("store down")

        responder = RecallResponder(
            None,
            MemoryConfig(nats_url="nats://localhost:4222"),
            Boom(),
        )
        raw = envelope.pack(
            "test",
            "trace-9",
            memory_pb2.RecallRequest(agent_id="pi", query_text="tea", top_k=5),
        )
        msg = _Msg(raw)

        async def respond(data: bytes) -> None:
            msg.replied = data

        msg.respond = respond  # type: ignore[method-assign]
        await responder._on_request(msg)
        env, resp = envelope.unpack(msg.replied, memory_pb2.RecallResponse)
        assert env.trace_id == "trace-9"
        assert resp.meta.degraded is True
        assert len(resp.items) > 0

    asyncio.run(run())
