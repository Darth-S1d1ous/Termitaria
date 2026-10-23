"""把一份文档写进它所属 project 的图。

doc_id 形如 `{project}/{local}`。正文放 content_ref：是现存文件就读文件，否则当作正文。
主张是正文里较长的段落。每条主张 DERIVES_FROM 文档，项目根节点 CITES 文档。
"""
from __future__ import annotations

import os
import re

from google.protobuf.struct_pb2 import Struct

from termitaria.graph.v1 import graph_pb2
from termitaria.memory.v1 import memory_pb2

from .falkor_store import project_of
from .store import GraphStore


def claim_texts(body: str, limit: int = 6) -> list[str]:
    parts = []
    for para in re.split(r"\n+", body):
        para = " ".join(para.split())
        if len(para) < 40:
            continue
        parts.append(para[:500])
        if len(parts) >= limit:
            break
    if not parts and body.strip():
        parts.append(" ".join(body.split())[:500])
    return parts


def document_body(doc: memory_pb2.Document) -> str:
    ref = (doc.content_ref or "").strip()
    if ref and "\n" not in ref and len(ref) < 512 and os.path.isfile(ref):
        with open(ref, encoding="utf-8", errors="replace") as handle:
            return handle.read(20000)
    if ref:
        return ref[:20000]
    return doc.title


def _props(text: str) -> Struct:
    props = Struct()
    if text:
        props.update({"text": text[:2000]})
    return props


async def ingest_document(store: GraphStore, doc: memory_pb2.Document) -> str:
    project = project_of(doc.doc_id)
    local = doc.doc_id.split("/", 1)[1] if project else (doc.doc_id or "anon")
    if not project:
        project = "default"
    doc_id = f"{project}/{local}"
    body = document_body(doc)
    kind = graph_pb2.NODE_KIND_DOCUMENT
    if doc.mime_type == "application/link":
        kind = graph_pb2.NODE_KIND_PAPER
    root_id = f"{project}/root"
    await store.upsert_node(
        graph_pb2.Node(
            node_id=root_id,
            kind=graph_pb2.NODE_KIND_CONCEPT,
            title="Project sources",
            props=_props("Sources ingested for this project."),
        )
    )
    await store.upsert_node(
        graph_pb2.Node(
            node_id=doc_id,
            kind=kind,
            title=doc.title or local,
            uri=doc.uri,
            props=_props(body),
        )
    )
    await store.upsert_edge(
        graph_pb2.Edge(
            edge_id=f"{doc_id}/cited",
            from_node_id=root_id,
            to_node_id=doc_id,
            kind=graph_pb2.EDGE_KIND_CITES,
        )
    )
    for index, text in enumerate(claim_texts(body)):
        claim_id = f"{doc_id}/c{index}"
        await store.upsert_node(
            graph_pb2.Node(
                node_id=claim_id,
                kind=graph_pb2.NODE_KIND_CLAIM,
                title=text[:80],
                props=_props(text),
            )
        )
        await store.upsert_edge(
            graph_pb2.Edge(
                edge_id=f"{claim_id}/derives",
                from_node_id=claim_id,
                to_node_id=doc_id,
                kind=graph_pb2.EDGE_KIND_DERIVES_FROM,
            )
        )
    return doc_id
