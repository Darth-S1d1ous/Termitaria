"""客户端 API：项目、聊天排序、以及走 ModelPort 的一问一答。不打网络。"""
import asyncio
from pathlib import Path

import pytest

from services.clientapi.converse import ChatDisabled, send_message
from services.clientapi.model_ref import model_ref
from services.clientapi.order import SAVED_CHAT_ID, order_chats
from services.clientapi.server import ClientApp
from services.clientapi.store import ProjectStore
from services.clientapi.swarm_loader import load_swarm
from services.clientapi.types import Agent
from services.worker.model import Chunk, Done
from termitaria.task.v1 import task_pb2


EXAMPLES = Path("examples/swarms")


def _agent(agent_id: str, display_name: str, kind: str) -> Agent:
    return Agent(
        id=agent_id,
        display_name=display_name,
        kind=kind,
        role_prompt=f"prompt-{agent_id}",
    )


def test_chats_pin_orchestrator_then_saved_then_alpha():
    rows = order_chats(
        [
            _agent("reviewer", "Skeptical Reviewer", "WORKER"),
            _agent("collector", "Literature Collector", "WORKER"),
            _agent("pi", "Principal Investigator", "ORCHESTRATOR"),
            _agent("ideator", "Idea Generator", "WORKER"),
        ]
    )
    assert [row.id for row in rows] == [
        "pi",
        SAVED_CHAT_ID,
        "ideator",
        "collector",
        "reviewer",
    ]
    assert rows[0].pinned is True
    assert rows[0].agent_kind == "ORCHESTRATOR"
    assert rows[1].kind == "saved"
    assert rows[1].enabled is False
    assert rows[1].title == "Saved Messages"
    assert all(row.kind == "agent" and row.pinned is False for row in rows[2:])


def test_workers_sort_by_display_name_casefold():
    rows = order_chats(
        [
            _agent("orch", "Zed", "ORCHESTRATOR"),
            _agent("b", "beta", "WORKER"),
            _agent("a", "Alpha", "WORKER"),
        ]
    )
    assert [row.id for row in rows] == ["orch", SAVED_CHAT_ID, "a", "b"]


def test_loader_reads_autoresearch_swarm():
    project = load_swarm(EXAMPLES / "autoresearch.yaml")
    assert project.id == "autoresearch"
    kinds = [agent.kind for agent in project.agents]
    assert kinds.count("ORCHESTRATOR") == 1
    assert project.agent("pi").display_name == "Principal Investigator"
    assert "PI" in project.agent("pi").role_prompt


def test_store_seeds_swarms_and_keeps_projects_independent(tmp_path: Path):
    store = ProjectStore.open(tmp_path / "state.json", EXAMPLES)
    seeded = store.list_projects()
    assert [project.id for project in seeded] == ["autoresearch"]

    created = store.create_project("Field Lab", goal="map the mound")
    assert created.id != "autoresearch"
    assert created.name == "Field Lab"
    assert created.goal == "map the mound"
    assert {agent.id for agent in created.agents} == {
        agent.id for agent in store.get_project("autoresearch").agents
    }

    created.agents[0].role_prompt = "mutated"
    reloaded = ProjectStore.open(tmp_path / "state.json", EXAMPLES)
    assert reloaded.get_project(created.id).agents[0].role_prompt != "mutated"
    assert reloaded.get_project("autoresearch").goal.startswith("给定用户的文献库")

    reloaded.delete_project(created.id)
    assert [project.id for project in reloaded.list_projects()] == ["autoresearch"]
    with pytest.raises(KeyError):
        reloaded.get_project(created.id)


def test_create_project_rejects_blank_name(tmp_path: Path):
    store = ProjectStore.open(tmp_path / "state.json", EXAMPLES)
    with pytest.raises(ValueError):
        store.create_project("   ")


class RecordingModel:
    def __init__(self, text: str = "pong"):
        self.text = text
        self.system = ""
        self.window: list[tuple[str, str]] = []
        self.max_tokens = 0

    async def stream(self, *, system, window, model, max_tokens):
        self.system = system
        self.window = list(window)
        self.max_tokens = max_tokens
        assert model.model
        yield Chunk(self.text)
        yield Done(task_pb2.TaskUsage(model=model.model))


def test_send_message_uses_role_prompt_and_persists_turn(tmp_path: Path):
    store = ProjectStore.open(tmp_path / "state.json", EXAMPLES)
    model = RecordingModel("391")
    user, agent = asyncio.run(
        send_message(store, "autoresearch", "pi", "17 times 23?", model, max_tokens=1024)
    )

    assert user.role == "user"
    assert user.text == "17 times 23?"
    assert agent.role == "agent"
    assert agent.text == "391"
    assert model.system == store.get_project("autoresearch").agent("pi").role_prompt
    assert model.window == [("user", "17 times 23?")]
    assert model.max_tokens == 1024

    again = RecordingModel("follow-up")
    asyncio.run(send_message(store, "autoresearch", "pi", "thanks", again, max_tokens=128))
    assert again.window == [
        ("user", "17 times 23?"),
        ("pi", "391"),
        ("user", "thanks"),
    ]
    roles = [item.role for item in store.list_messages("autoresearch", "pi")]
    assert roles == ["user", "agent", "user", "agent"]


def test_saved_messages_and_model_errors_do_not_write(tmp_path: Path):
    store = ProjectStore.open(tmp_path / "state.json", EXAMPLES)

    class BoomModel:
        async def stream(self, **kwargs):
            raise RuntimeError("down")
            yield Chunk("nope")

    with pytest.raises(ChatDisabled):
        asyncio.run(send_message(store, "autoresearch", SAVED_CHAT_ID, "hi", RecordingModel()))
    with pytest.raises(RuntimeError):
        asyncio.run(send_message(store, "autoresearch", "ideator", "hi", BoomModel()))
    assert store.list_messages("autoresearch", SAVED_CHAT_ID) == []
    assert store.list_messages("autoresearch", "ideator") == []


def test_list_chats_previews_last_message_without_peer_sessions(tmp_path: Path):
    store = ProjectStore.open(tmp_path / "state.json", EXAMPLES)
    asyncio.run(send_message(store, "autoresearch", "ideator", "hello", RecordingModel("idea"), max_tokens=32))
    chats = store.list_chats("autoresearch")
    assert [chat.id for chat in chats] == [
        "pi",
        SAVED_CHAT_ID,
        "experimenter",
        "ideator",
        "collector",
        "reviewer",
    ]
    ideator = next(chat for chat in chats if chat.id == "ideator")
    assert ideator.last_message == "idea"
    assert all(chat.id != "reviewer-ideator" for chat in chats)


def test_http_round_trip_and_saved_rejection(tmp_path: Path):
    store = ProjectStore.open(tmp_path / "state.json", EXAMPLES)
    app = ClientApp(store, RecordingModel("from-model"), max_tokens=64)

    status, created = app.handle("POST", "/api/projects", {"name": "Second", "goal": "g"})
    assert status == 201
    assert created["name"] == "Second"

    status, listing = app.handle("GET", "/api/projects", None)
    assert status == 200
    assert {item["id"] for item in listing["projects"]} == {"autoresearch", created["id"]}

    status, chats = app.handle("GET", "/api/projects/autoresearch/chats", None)
    assert status == 200
    assert chats["chats"][0]["id"] == "pi"
    assert chats["chats"][1]["id"] == SAVED_CHAT_ID
    assert chats["chats"][1]["enabled"] is False

    status, denied = app.handle(
        "POST",
        f"/api/projects/autoresearch/chats/{SAVED_CHAT_ID}/messages",
        {"text": "nope"},
    )
    assert status == 409
    assert denied["error"]

    status, turn = app.handle(
        "POST",
        "/api/projects/autoresearch/chats/pi/messages",
        {"text": "ping"},
    )
    assert status == 200
    assert turn["user"]["text"] == "ping"
    assert turn["agent"]["text"] == "from-model"

    status, history = app.handle("GET", "/api/projects/autoresearch/chats/pi/messages", None)
    assert status == 200
    assert [item["role"] for item in history["messages"]] == ["user", "agent"]

    status, missing = app.handle("DELETE", f"/api/projects/{created['id']}", None)
    assert status == 204
    assert missing is None
    status, _ = app.handle("DELETE", f"/api/projects/{created['id']}", None)
    assert status == 404


def test_model_ref_reads_nvidia_env(monkeypatch):
    monkeypatch.setenv("NVIDIA_MODEL", "moonshotai/kimi-k3")
    ref = model_ref()
    assert ref.provider == "nvidia"
    assert ref.model == "moonshotai/kimi-k3"

    monkeypatch.delenv("NVIDIA_MODEL")
    assert model_ref().model == "stub-model"
