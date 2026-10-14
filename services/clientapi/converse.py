"""用户和单个 agent 的一轮对话。模型出口与 worker 相同，不经记忆召回。"""
from __future__ import annotations

from services.worker.model import Chunk

from .model_ref import model_ref
from .order import SAVED_CHAT_ID
from .store import ProjectStore
from .types import Message


class ChatDisabled(Exception):
    """Saved Messages 只有位置，没有发送。"""


async def send_message(
    store: ProjectStore,
    project_id: str,
    chat_id: str,
    text: str,
    model,
    max_tokens: int = 16384,
) -> tuple[Message, Message]:
    if chat_id == SAVED_CHAT_ID:
        raise ChatDisabled("saved messages are not available")
    project = store.get_project(project_id)
    agent = project.agent(chat_id)
    window = [_window_item(agent.id, message) for message in store.list_messages(project_id, chat_id)]
    window.append(("user", text))
    parts: list[str] = []
    async for event in model.stream(
        system=agent.role_prompt,
        window=window,
        model=model_ref(),
        max_tokens=max_tokens,
    ):
        if isinstance(event, Chunk):
            parts.append(event.text)
    reply = "".join(parts).strip()
    if not reply:
        raise RuntimeError("model returned no content")
    return store.append_turn(project_id, chat_id, text, reply)


def _window_item(agent_id: str, message: Message) -> tuple[str, str]:
    if message.role == "user":
        return ("user", message.text)
    return (agent_id, message.text)
