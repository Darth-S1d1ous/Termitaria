"""session 关闭时，给每个参与 agent 写一条情节。

记忆服务自己订阅 sessions.*。情节是这段会话的逐句记录，只写入该 agent 自己的库。
助手的话带上发言者，不写成没有出处的事实。会话开着的时候不写，所以不挡住聊天。
"""
from __future__ import annotations

import logging

from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from termitaria.memory.v1 import memory_pb2
from termitaria.session.v1 import session_pb2

from services.common import envelope

from .config import MemoryConfig

log = logging.getLogger(__name__)

_STREAM = "SESSIONS"
_DURABLE = "memory-distiller"
_WRITE_EPISODE = "memory.write.episode"
_MAX_LINES = 40


class Distiller:
    def __init__(self, nc, cfg: MemoryConfig) -> None:
        self._js = nc.jetstream()
        self._cfg = cfg
        # session_id → agent participants。启动时重放 Opened / Closed 重建。
        self._participants: dict[str, list[str]] = {}
        # session_id → 本段会话的逐句记录。关闭时才发出。
        self._lines: dict[str, list[str]] = {}
        # 已发出的 (session_id, agent_id)，测试断言口。
        self.fanned_out: list[tuple[str, str]] = []

    async def run(self) -> None:
        await self._recover_participants()
        sub = await self._js.pull_subscribe("sessions.>", durable=_DURABLE, stream=_STREAM)
        log.info("distiller up: durable=%s filter=sessions.> open_sessions=%d", _DURABLE, len(self._participants))
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
        if ev.WhichOneof("event") == "closed":
            await self._distill(ev, env.trace_id)
        self._apply_roster(ev)
        if ev.WhichOneof("event") == "message_appended":
            self._note(ev)
        await msg.ack()

    async def _recover_participants(self) -> None:
        # 临时消费者从头读，不碰 memory-distiller 的游标，也不把旧会话再写一遍。
        sub = await self._js.pull_subscribe(
            "sessions.>",
            stream=_STREAM,
            config=ConsumerConfig(
                deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.NONE,
                filter_subject="sessions.>",
            ),
        )
        try:
            while True:
                try:
                    msgs = await sub.fetch(self._cfg.fetch_batch, timeout=self._cfg.fetch_timeout_s)
                except Exception as exc:
                    if type(exc).__name__ != "TimeoutError":
                        log.exception("replay sessions")
                    break
                if not msgs:
                    break
                for msg in msgs:
                    self._remember_roster(msg)
        finally:
            await sub.unsubscribe()
        log.info("distiller recovered %d open sessions", len(self._participants))

    def _remember_roster(self, msg) -> None:
        try:
            _, ev = envelope.unpack(msg.data, session_pb2.SessionEvent)
        except Exception:
            log.exception("unparseable session event during replay")
            return
        self._apply_roster(ev)
        self._note(ev)

    def _apply_roster(self, ev: session_pb2.SessionEvent) -> None:
        kind = ev.WhichOneof("event")
        if kind == "opened":
            agents = [p.agent_id for p in ev.opened.session.participants if p.HasField("agent_id")]
            self._participants[ev.session_id] = agents
            self._lines.setdefault(ev.session_id, [])
        elif kind == "closed":
            self._participants.pop(ev.session_id, None)
            self._lines.pop(ev.session_id, None)

    def _note(self, ev: session_pb2.SessionEvent) -> None:
        if ev.WhichOneof("event") != "message_appended":
            return
        if ev.session_id not in self._participants:
            return
        line = _line(ev.message_appended.message)
        if not line:
            return
        bucket = self._lines.setdefault(ev.session_id, [])
        if len(bucket) < _MAX_LINES:
            bucket.append(line)

    async def _distill(self, ev: session_pb2.SessionEvent, trace_id: str) -> None:
        agents = self._participants.get(ev.session_id, [])
        lines = list(self._lines.get(ev.session_id, []))
        summary = "\n".join(lines).strip()
        if not summary:
            return
        log.info(
            "session closed %s → episode for %d agents (trace=%s)",
            ev.session_id, len(agents), trace_id,
        )
        for agent_id in agents:
            episode = memory_pb2.Episode(
                agent_id=agent_id,
                session_id=ev.session_id,
                summary=summary,
                salient_spans=lines[:12],
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
            log.info("wrote episode agent=%s session=%s", agent_id, ev.session_id)


def _line(message: session_pb2.Message) -> str:
    text = _message_text(message)
    if not text:
        return ""
    speaker = getattr(message, "from")
    kind = speaker.WhichOneof("kind")
    if kind == "user_id":
        who = "user"
    elif kind == "agent_id":
        who = speaker.agent_id or "agent"
    else:
        who = "note"
    return f"{who}: {text}"


def _message_text(message: session_pb2.Message) -> str:
    parts = []
    for block in message.content:
        if block.WhichOneof("block") == "text" and block.text:
            parts.append(block.text)
    return "\n".join(parts)
