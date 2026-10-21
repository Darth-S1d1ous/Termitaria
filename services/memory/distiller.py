"""Episode 自沉淀（路线图决策 1 / P3）：SessionClosed → 按 participants fan-out。

记忆服务自己订阅 sessions.*。session 开着时记下消息正文。
关闭时给每个参与 agent 发一条 memory.write.episode。
摘要是拼起来的原文，抽取交给选中的后端（Mem0 / Graphiti），这里不再调一次 LLM。
推理期内不写（recall before dispatch, not during inference）。
"""
from __future__ import annotations

import logging

from termitaria.memory.v1 import memory_pb2
from termitaria.session.v1 import session_pb2

from services.common import envelope

from .config import MemoryConfig

log = logging.getLogger(__name__)

_STREAM = "SESSIONS"
_DURABLE = "memory-distiller"
_WRITE_EPISODE = "memory.write.episode"


class Distiller:
    def __init__(self, nc, cfg: MemoryConfig) -> None:
        self._js = nc.jetstream()
        self._cfg = cfg
        # session_id → agent participants。内存表 best-effort：
        # TODO(colleague)：进程重启后丢失，正式实现应对该 session 做事件重放
        # （参考 internal/session/manager.go 的 replay）取回 Opened 事件。
        self._participants: dict[str, list[str]] = {}
        # session_id → 已见到的消息正文。和 participants 一样，重启后丢失。
        self._lines: dict[str, list[str]] = {}
        # 已 fan-out 的 (session_id, agent_id)，测试断言口。
        self.fanned_out: list[tuple[str, str]] = []

    async def run(self) -> None:
        sub = await self._js.pull_subscribe("sessions.>", durable=_DURABLE, stream=_STREAM)
        log.info("distiller up: durable=%s filter=sessions.>", _DURABLE)
        while True:
            try:
                msgs = await sub.fetch(self._cfg.fetch_batch, timeout=self._cfg.fetch_timeout_s)
            except Exception as exc:
                if type(exc).__name__ != "TimeoutError":
                    log.exception("fetch sessions")
                continue
            for msg in msgs:
                await self._handle(msg)
                
    async def _handle(self, msg) -> None:
        try:
            env, ev = envelope.unpack(msg.data, session_pb2.SessionEvent)
        except Exception:
            log.exception("unparseable session event, ack to drop")
            await msg.ack()
            return
        kind = ev.WhichOneof("event")
        if kind == "opened":
            agents = [p.agent_id for p in ev.opened.session.participants if p.HasField("agent_id")]
            self._participants[ev.session_id] = agents
            self._lines.setdefault(ev.session_id, [])
        elif kind == "message_appended":
            text = _message_text(ev.message_appended.message)
            if text:
                self._lines.setdefault(ev.session_id, []).append(text)
        elif kind == "closed":
            await self._distill(ev, env.trace_id)
            self._participants.pop(ev.session_id, None)
            self._lines.pop(ev.session_id, None)
        await msg.ack()

    async def _distill(self, ev: session_pb2.SessionEvent, trace_id: str) -> None:
        agents = self._participants.get(ev.session_id, [])
        log.info(
            "session closed: %s (swarm=%s reason=%r) → fan-out 蒸馏 %d 个 agent (trace=%s)",
            ev.session_id, ev.swarm_id, ev.closed.reason, len(agents), trace_id,
        )
        lines = list(self._lines.get(ev.session_id, []))
        summary = "\n".join(lines)
        for agent_id in agents:
            episode = memory_pb2.Episode(
                agent_id=agent_id,
                session_id=ev.session_id,
                summary=summary,
                salient_spans=lines,
                occurred_at_ms=envelope.now_ms(),
            )
            payload = envelope.pack(
                self._cfg.producer,
                trace_id,
                memory_pb2.WriteEpisodeRequest(episode=episode),
            )
            try:
                await self._js.publish(_WRITE_EPISODE, payload)
            except Exception:
                log.exception(
                    "publish episode agent=%s session=%s", agent_id, ev.session_id
                )
                continue
            self.fanned_out.append((ev.session_id, agent_id))
            log.info("distilled episode agent=%s session=%s", agent_id, ev.session_id)


def _message_text(message: session_pb2.Message) -> str:
    parts = []
    for block in message.content:
        if block.WhichOneof("block") == "text" and block.text:
            parts.append(block.text)
    return "\n".join(parts)