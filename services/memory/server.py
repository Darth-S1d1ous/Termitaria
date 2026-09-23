"""总线集成外壳：recall 应答 + write 落盘（contracts §2 的 memory.* subject）。

- recall：core NATS request-reply（同步、有 Budget 约束；架构 §6.3）；
- write：JetStream pull（异步、至少一次；event_id 去重，contracts §1.3）；
- 落盘 JSONL 是 P1 的观测手段，同事接内核时换成 Postgres/Qdrant 写入。

TODO(p2)：envelope 目前复用 services.worker，两包稳定后可上移到 services/common/。
"""
from __future__ import annotations

import asyncio
import logging
import os

from google.protobuf.json_format import MessageToJson
from google.protobuf.message import Message as PbMessage

from termitaria.memory.v1 import memory_pb2

from services.worker import envelope

from .canned import build_recall_response
from .config import MemoryConfig

log = logging.getLogger(__name__)

_STREAM = "MEMORY"
_RECALL_SUBJECT = "memory.recall"
_RECALL_QUEUE = "memory-service"  # queue group：将来多副本负载均衡

class RecallResponder:
    """memory.recall 的 request-reply 应答；Go dispatcher 预取（P2）的对端。"""
    def __init__(self, nc, cfg: MemoryConfig) -> None:
        self._nc = nc
        self._cfg = cfg

    async def run(self) -> None:
        await self._nc.subscribe(_RECALL_SUBJECT, queue=_RECALL_QUEUE, cb=self._on_request)
        log.info("recall responder up: %s (queue=%s)", _RECALL_SUBJECT, _RECALL_QUEUE)
        await asyncio.Event().wait()  # 常驻；回调驱动

    async def _on_request(self, msg) -> None:
        try:
            env, req = envelope.unpack(msg.data, memory_pb2.RecallRequest)
        except Exception:
            # 坏消息也要应答空响应——request-reply 的调用方在等，不应答就是超时
            log.exception("unparseable recall request, reply empty")
            await msg.respond(envelope.pack(self._cfg.producer, "", memory_pb2.RecallResponse()))
            return

        resp = build_recall_response(req)
        await msg.respond(envelope.pack(self._cfg.producer, env.trace_id, resp))
        log.info(
            "recall agent=%s depth=%s → %d items (trace=%s)",
            req.agent_id,
            memory_pb2.RecallDepth.Name(req.depth),
            len(resp.items),
            env.trace_id,
        )

class WriteConsumer:
    """memory.write.* 的 JetStream pull consumer：去重后落盘 JSONL。

    一个实例管一个 subject（episode / document 各一个 durable，游标独立）。
    """

    def __init__(
        self,
        nc,
        cfg: MemoryConfig,
        *,
        subject: str,
        durable: str,
        payload_type: type[PbMessage],
        spool_file: str,
    ) -> None:
        self._js = nc.jetstream()
        self._cfg = cfg
        self._subject = subject
        self._durable = durable
        self._type = payload_type
        self._path = os.path.join(cfg.spool_dir, spool_file)
        self._seen: set[str] = set()  # event_id 内存级去重；MVP 不持久化

    async def run(self) -> None:
        os.makedirs(self._cfg.spool_dir, exist_ok=True)
        sub = await self._js.pull_subscribe(self._subject, durable=self._durable, stream=_STREAM)
        log.info("write consumer up: durable=%s filter=%s → %s", self._durable, self._subject, self._path)

        while True:
            try:
                msgs = await sub.fetch(self._cfg.fetch_batch, timeout=self._cfg.fetch_timeout_s)
            except Exception as exc:  # TimeoutError 是常态，其余记录后继续
                if type(exc).__name__ != "TimeoutError":
                    log.exception("fetch %s", self._subject)
                continue

            for msg in msgs:
                await self._handle(msg)

    async def _handle(self, msg) -> None:
        try:
            env, payload = envelope.unpack(msg.data, self._type)
        except Exception:
            log.exception("unparseable %s message, ack to drop", self._subject)
            await msg.ack()
            return
        if env.event_id in self._seen:
            log.info("duplicate event_id=%s, ack", env.event_id)
            await msg.ack()
            return
        
        line = {
            "event_id": env.event_id,
            "trace_id": env.trace_id,
            "received_at_ms": envelope.now_ms(),
            "payload": MessageToJson(payload),
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(__import__("json").dumps(line, ensure_ascii=False) + "\n")
        self._seen.add(env.event_id)
        await msg.ack()
        log.info("spooled %s event=%s → %s", self._type.DESCRIPTOR.full_name, env.event_id, self._path)