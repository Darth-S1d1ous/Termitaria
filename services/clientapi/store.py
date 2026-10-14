"""项目与用户可见聊天记录。每个项目一份 agent 名册，互不共享。"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path

from .order import order_chats
from .swarm_loader import load_swarm
from .types import ChatRow, Message, Project


class ProjectStore:
    def __init__(self, state_path: Path, template: Project) -> None:
        self._path = state_path
        self._template = copy.deepcopy(template)
        self._lock = threading.RLock()
        self._projects: dict[str, Project] = {}
        self._messages: dict[str, dict[str, list[Message]]] = {}

    @classmethod
    def open(cls, state_path: Path, swarm_dir: Path) -> "ProjectStore":
        specs = [load_swarm(path) for path in sorted(swarm_dir.glob("*.yaml"))]
        if not specs:
            raise FileNotFoundError(f"no swarm specs in {swarm_dir}")
        template = next((spec for spec in specs if spec.id == "autoresearch"), specs[0])
        store = cls(state_path, template)
        if state_path.exists():
            store._load()
        else:
            for spec in specs:
                store._projects[spec.id] = spec
            store._save()
        return store

    def list_projects(self) -> list[Project]:
        with self._lock:
            return [copy.deepcopy(project) for project in self._projects.values()]

    def get_project(self, project_id: str) -> Project:
        with self._lock:
            project = self._projects.get(project_id)
            if project is None:
                raise KeyError(project_id)
            return copy.deepcopy(project)

    def create_project(self, name: str, goal: str = "") -> Project:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        project = Project(
            id="p_" + uuid.uuid4().hex[:12],
            name=name,
            goal=goal.strip() or self._template.goal,
            agents=copy.deepcopy(self._template.agents),
        )
        with self._lock:
            self._projects[project.id] = project
            self._save()
            return copy.deepcopy(project)

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(project_id)
            del self._projects[project_id]
            self._messages.pop(project_id, None)
            self._save()

    def list_chats(self, project_id: str) -> list[ChatRow]:
        project = self.get_project(project_id)
        rows: list[ChatRow] = []
        with self._lock:
            for row in order_chats(project.agents):
                history = self._messages.get(project_id, {}).get(row.id, [])
                last = history[-1] if history else None
                if last is None:
                    rows.append(row)
                    continue
                rows.append(
                    ChatRow(
                        id=row.id,
                        kind=row.kind,
                        title=row.title,
                        agent_kind=row.agent_kind,
                        pinned=row.pinned,
                        enabled=row.enabled,
                        last_message=last.text,
                        last_role=last.role,
                        last_message_at_ms=last.created_at_ms,
                    )
                )
        return rows

    def list_messages(self, project_id: str, chat_id: str) -> list[Message]:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(project_id)
            return list(self._messages.get(project_id, {}).get(chat_id, []))

    def append_turn(self, project_id: str, chat_id: str, text: str, reply: str) -> tuple[Message, Message]:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(project_id)
            now = int(time.time() * 1000)
            user = Message(id=_message_id(), role="user", text=text, created_at_ms=now)
            agent = Message(id=_message_id(), role="agent", text=reply, created_at_ms=now + 1)
            self._messages.setdefault(project_id, {}).setdefault(chat_id, []).extend([user, agent])
            self._save()
            return user, agent

    def _load(self) -> None:
        raw = json.loads(self._path.read_text())
        self._projects = {}
        for item in raw.get("projects", []):
            project = Project(
                id=item["id"],
                name=item["name"],
                goal=item["goal"],
                agents=[
                    _agent_from(agent)
                    for agent in item["agents"]
                ],
            )
            self._projects[project.id] = project
        self._messages = {
            project_id: {
                chat_id: [_message_from(item) for item in messages]
                for chat_id, messages in chats.items()
            }
            for project_id, chats in raw.get("messages", {}).items()
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "projects": [_project_to_dict(project) for project in self._projects.values()],
            "messages": {
                project_id: {
                    chat_id: [_message_to_dict(message) for message in messages]
                    for chat_id, messages in chats.items()
                }
                for project_id, chats in self._messages.items()
            },
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(self._path)


def _message_id() -> str:
    return "m_" + uuid.uuid4().hex[:16]


def _agent_from(raw: dict):
    from .types import Agent

    return Agent(
        id=raw["id"],
        display_name=raw["display_name"],
        kind=raw["kind"],
        role_prompt=raw["role_prompt"],
    )


def _project_to_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "goal": project.goal,
        "agents": [
            {
                "id": agent.id,
                "display_name": agent.display_name,
                "kind": agent.kind,
                "role_prompt": agent.role_prompt,
            }
            for agent in project.agents
        ],
    }


def _message_from(raw: dict) -> Message:
    return Message(
        id=raw["id"],
        role=raw["role"],
        text=raw["text"],
        created_at_ms=raw["created_at_ms"],
    )


def _message_to_dict(message: Message) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "text": message.text,
        "created_at_ms": message.created_at_ms,
    }
