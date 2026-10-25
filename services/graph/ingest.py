"""memory.write.document → Graphiti。

独立 durable（graph-ingest）。MEMORY stream 是 LimitsPolicy，与 memory 服务的
观测 consumer 各收一份，互不抢消息。
"""
from __future__ import annotations

import json
import logging
import os

from google.protobuf.json_format import MessageToJson

from termitaria.memory.v1 import memory_pb2

from services.common import envelope

from .config import GraphConfig
from .graphiti_kg import project_of

log = logging.getLogger(__name__)

_STREAM = "MEMORY"
_SUBJECT = "memory.write.document"
_DURABLE = "graph-ingest"


def document_node_id(doc_id: str) -> str:
    """确定性 id：ingest 重试覆盖同一节点（路线图决策 3）。"""
    return "gn_doc_" + doc_id


def document_body(doc: memory_pb2.Document) -> str:
    ref = (doc.content_ref or "").strip()
    if ref and "\n" not in ref and len(ref) < 512 and os.path.isfile(ref):
        with open(ref, encoding="utf-8", errors="replace") as handle:
            return handle.read(20000)
    if ref:
        return ref[:20000]
    return doc.title


class Ingestor:
    def __init__(self, nc, cfg: GraphConfig, engine) -> None:
        self._js = nc.jetstream()
        self._cfg = cfg
        self._engine = engine
        self._path = os.path.join(cfg.spool_dir, "documents.jsonl")
        self._seen: set[str] = set()  # event_id 内存级去重；MVP 不持久化
        self.ingested: list[str] = []  # 已落图的 node_id，测试断言口

    async def run(self) -> None:
        os.makedirs(self._cfg.spool_dir, exist_ok=True)
        sub = await self._js.pull_subscribe(_SUBJECT, durable=_DURABLE, stream=_STREAM)
        log.info("ingest up: durable=%s filter=%s → %s", _DURABLE, _SUBJECT, self._path)
        while True:
            try:
                msgs = await sub.fetch(self._cfg.fetch_batch, timeout=self._cfg.fetch_timeout_s)
            except Exception as exc:
                if type(exc).__name__ != "TimeoutError":
                    log.exception("fetch %s", _SUBJECT)
                continue
            for msg in msgs:
                await self._handle(msg)

    async def _handle(self, msg) -> None:
        try:
            env, req = envelope.unpack(msg.data, memory_pb2.WriteDocumentRequest)
        except Exception:
            log.exception("unparseable %s, ack to drop", _SUBJECT)
            await msg.ack()
            return
        if env.event_id in self._seen:
            log.info("duplicate event_id=%s, ack", env.event_id)
            await msg.ack()
            return
        node_id = await self._ingest(req.document, env.trace_id)
        self._spool(env, req)
        self._seen.add(env.event_id)
        self.ingested.append(node_id)
        await msg.ack()
        log.info("ingested doc=%s → %s (trace=%s)", req.document.doc_id, node_id, env.trace_id)

    async def _ingest(self, doc: memory_pb2.Document, trace_id: str) -> str:
        log.info("ingest doc=%s trace=%s", doc.doc_id, trace_id)
        project = project_of(doc.doc_id) or "default"
        text = document_body(doc)
        try:
            await self._engine.add_text(project, doc.title or doc.doc_id, text)
        except Exception:
            log.exception("graphiti extract failed doc=%s", doc.doc_id)
        return doc.doc_id

    def _spool(self, env, req: memory_pb2.WriteDocumentRequest) -> None:
        line = {
            "event_id": env.event_id,
            "trace_id": env.trace_id,
            "received_at_ms": envelope.now_ms(),
            "payload": MessageToJson(req),
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
