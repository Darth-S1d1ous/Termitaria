"""聊天列表：orchestrator 置顶，Saved Messages 第二，其余 agent 按显示名排序。

agent 之间的 session 不是聊天行。
"""
from __future__ import annotations

from .types import Agent, ChatRow

SAVED_CHAT_ID = "saved"
SAVED_TITLE = "Saved Messages"


def order_chats(agents: list[Agent]) -> list[ChatRow]:
    def sort_key(agent: Agent) -> tuple[str, str]:
        return (agent.display_name.casefold(), agent.id)

    orchestrators = sorted((agent for agent in agents if agent.kind == "ORCHESTRATOR"), key=sort_key)
    workers = sorted((agent for agent in agents if agent.kind != "ORCHESTRATOR"), key=sort_key)
    rows = [_agent_row(agent, pinned=True) for agent in orchestrators]
    rows.append(
        ChatRow(
            id=SAVED_CHAT_ID,
            kind="saved",
            title=SAVED_TITLE,
            agent_kind="",
            pinned=False,
            enabled=False,
        )
    )
    rows.extend(_agent_row(agent, pinned=False) for agent in workers)
    return rows


def _agent_row(agent: Agent, *, pinned: bool) -> ChatRow:
    return ChatRow(
        id=agent.id,
        kind="agent",
        title=agent.display_name,
        agent_kind=agent.kind,
        pinned=pinned,
        enabled=True,
    )
