"""Episode 自沉淀占位（路线图决策 1 / P3）：SessionClosed → 按 participants fan-out 蒸馏。

设计对标 Google ADK add_session_to_memory / Mem0 / Zep 的「记忆层自沉淀」模式：
记忆服务自己订阅 sessions.*，session 关闭时按参与者各沉淀一条 per-agent Episode，
自己调 LLM summarize——推理期内不自写（recall before dispatch, not during inference）。

本文件是 stub：只完成订阅、participants 跟踪与 fan-out 日志。
TODO(colleague) 的三步填实位置见 _distill。
"""
from __future__ import annotations

import logging

from termitaria.session.v1 import session_pb2

from services.worker import envelope

from .config import MemoryConfig

log = logging.getLogger(__name__)

_STREAM = "SESSIONS"
_DURABLE = "memory-distiller"


class Distiller:
    def __init__(self, nc, cfg: MemoryConfig) -> None:
        self._js = nc.jetstream()
        self._cfg = cfg
        # session_id → agent participants。内存表 best-effort：
        # TODO(colleague)：进程重启后丢失，正式实现应对该 session 做事件重放
        # （参考 internal/session/manager.go 的 replay）取回 Opened 事件。
        self._participants: dict[str, list[str]] = {}
        # 已 fan-out 的 (session_id, agent_id)，stub 的观测点，也是测试断言口。
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
        elif kind == "closed":
            await self._distill(ev, env.trace_id)
            self._participants.pop(ev.session_id, None)
        # message_appended / turn_advanced / suspended：沉淀不需要，直接 ack
        await msg.ack()

    async def _distill(self, ev: session_pb2.SessionEvent, trace_id: str) -> None:
        agents = self._participants.get(ev.session_id, [])
        log.info(
            "session closed: %s (swarm=%s reason=%r) → fan-out 蒸馏 %d 个 agent (trace=%s)",
            ev.session_id, ev.swarm_id, ev.closed.reason, len(agents), trace_id,
        )
        for agent_id in agents:
            self.fanned_out.append((ev.session_id, agent_id))
            # TODO(colleague)：P3 填实——
            # 1. 重放 sessions.<swarm>.<session>.events 取 transcript；
            # 2. LLM summarize → Episode(agent_id, session_id, summary, salient_spans)；
            # 3. envelope.pack → 发布 memory.write.episode（显式写入口保留，contracts §2）。
            log.info("  [stub] would distill episode for agent=%s session=%s", agent_id, ev.session_id)