"""项目、agent 与聊天消息。一个项目持有自己的一份 swarm。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Agent:
    id: str
    display_name: str
    kind: str
    role_prompt: str


@dataclass
class Project:
    id: str
    name: str
    goal: str
    agents: list[Agent]

    def agent(self, agent_id: str) -> Agent:
        for item in self.agents:
            if item.id == agent_id:
                return item
        raise KeyError(agent_id)


@dataclass(frozen=True)
class Message:
    id: str
    role: str
    text: str
    created_at_ms: int


@dataclass(frozen=True)
class ChatRow:
    id: str
    kind: str
    title: str
    agent_kind: str
    pinned: bool
    enabled: bool
    last_message: str = ""
    last_role: str = ""
    last_message_at_ms: int = 0
