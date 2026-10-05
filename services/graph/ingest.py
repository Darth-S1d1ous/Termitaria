"""memory.write.document → KG 的 ingest stub（路线图决策 2：KG 的唯一写路径）。

独立 durable（graph-ingest）。MEMORY stream 是 LimitsPolicy，与 memory 服务的
观测 consumer 各收一份，互不抢消息。

TODO(colleague) 的四步在 _extract；本 stub 只做两件可观测的事：
落盘 JSONL，以及把文档本身 upsert 成 DOCUMENT 节点。
"""
from __future__ import annotations

import json
import logging
import os

from google.protobuf.json_format import MessageToJson

from termitaria.graph.v1 import graph_pb2
from termitaria.memory.v1 import memory_pb2

from services.common import envelope

from .config import GraphConfig
from .store import GraphStore

log = logging.getLogger(__name__)

_STREAM = "MEMORY"
_SUBJECT = "memory.write.document"
_DURABLE = "graph-ingest"

def document_node_id(doc_id: str) -> str:
    """确定性 id：ingest 重试覆盖同一节点（路线图决策 3）。"""
    return "gn_doc_" + doc_id


class Ingestor:
    def __init__(self, nc, cfg: GraphConfig, store: GraphStore) -> None:
        self._js = nc.jetstream()
        self._cfg = cfg
        self._store = store
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
        # TODO(colleague)：
        # 1. chunk：按 content_ref 取正文（大对象不走 bus，contracts §1.4）；
        # 2. embed；
        # 3. LLM 抽 claim / citation；
        # 4. claim 节点 id = hash(doc_id + chunk_index)，边 CITES / DERIVES_FROM upsert。
        log.info("[stub] skip extract doc=%s trace=%s", doc.doc_id, trace_id)
        node = graph_pb2.Node(
            node_id=document_node_id(doc.doc_id or "anon"),
            kind=graph_pb2.NODE_KIND_DOCUMENT,
            title=doc.title,
            uri=doc.uri,
        )
        return await self._store.upsert_node(node)

    def _spool(self, env, req: memory_pb2.WriteDocumentRequest) -> None:
        line = {
            "event_id": env.event_id,
            "trace_id": env.trace_id,
            "received_at_ms": envelope.now_ms(),
            "payload": MessageToJson(req),
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
