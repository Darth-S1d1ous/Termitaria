"""用户每说一句，就给会话里的每个 agent 写一条情节。

记忆服务自己订阅 sessions.*。助手的回复不写，避免把模型编造的话存成事实。
会话关闭时不再把整段重发一遍。
写入走 memory.write.episode，和网页请求不是同一条路，所以不挡住聊天。
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


class Distiller:
    def __init__(self, nc, cfg: MemoryConfig) -> None:
        self._js = nc.jetstream()
        self._cfg = cfg
        # session_id → agent participants。启动时重放 Opened / Closed 重建。
        self._participants: dict[str, list[str]] = {}
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
        self._apply_roster(ev)
        if ev.WhichOneof("event") == "message_appended":
            message = ev.message_appended.message
            text = _message_text(message)
            if text and _is_user(message):
                await self._write_line(ev, env.trace_id, text)
        await msg.ack()

    async def _recover_participants(self) -> None:
        # 临时消费者从头读，不碰 memory-distiller 的游标，也不把旧用户消息再写一遍。
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

    def _apply_roster(self, ev: session_pb2.SessionEvent) -> None:
        kind = ev.WhichOneof("event")
        if kind == "opened":
            agents = [p.agent_id for p in ev.opened.session.participants if p.HasField("agent_id")]
            self._participants[ev.session_id] = agents
        elif kind == "closed":
            self._participants.pop(ev.session_id, None)

    async def _write_line(self, ev: session_pb2.SessionEvent, trace_id: str, text: str) -> None:
        agents = self._participants.get(ev.session_id, [])
        log.info(
            "user line session=%s swarm=%s → write %d agents (trace=%s)",
            ev.session_id, ev.swarm_id, len(agents), trace_id,
        )
        for agent_id in agents:
            episode = memory_pb2.Episode(
                agent_id=agent_id,
                session_id=ev.session_id,
                summary=text,
                salient_spans=[text],
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


def _is_user(message: session_pb2.Message) -> bool:
    speaker = getattr(message, "from")
    return speaker.WhichOneof("kind") == "user_id"


def _message_text(message: session_pb2.Message) -> str:
    parts = []
    for block in message.content:
        if block.WhichOneof("block") == "text" and block.text:
            parts.append(block.text)
    return "\n".join(parts)
