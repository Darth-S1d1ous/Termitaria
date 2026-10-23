"""跨文件的主张边只连接不同文件，而且只接受 SUPPORTS / CONTRADICTS。"""
from __future__ import annotations

import asyncio
import json

import services
from termitaria.graph.v1 import graph_pb2
from termitaria.memory.v1 import memory_pb2

from services.graph.project_graph import ingest_document
from services.graph.relate import link_documents, parse_relations
from services.graph.store import InMemoryGraph

MEMO = "Northwire asked investors for 4.80 dollars per kilogram for 8 millimeter busbar wire delivered in Houston."
REPORT = "The trading brief repeats Northwire's 4.80 dollars per kilogram Houston quote and treats that figure as the recorded price."


def test_parse_relations_keeps_only_cross_file_support():
    docs = {
        "p/memo/c0": "p/memo",
        "p/report/c1": "p/report",
        "p/report/c2": "p/report",
    }
    raw = json.dumps([
        {"from": "p/report/c1", "to": "p/memo/c0", "relation": "SUPPORTS"},
        {"from": "p/report/c1", "to": "p/report/c2", "relation": "CONTRADICTS"},
        {"from": "p/missing", "to": "p/memo/c0", "relation": "SUPPORTS"},
        {"from": "p/report/c1", "to": "p/memo/c0", "relation": "CITES"},
    ])
    assert parse_relations(raw, docs, {"p/report/c1", "p/report/c2"}) == [
        ("p/report/c1", "p/memo/c0", "SUPPORTS")
    ]


def test_link_documents_writes_a_support_edge_between_files():
    store = InMemoryGraph()

    def fake(prompt: str) -> str:
        assert "p_alpha/memo/c0" in prompt
        assert "p_beta" not in prompt
        return json.dumps([
            {"from": "p_alpha/report/c0", "to": "p_alpha/memo/c0", "relation": "SUPPORTS"},
        ])

    async def run() -> None:
        await ingest_document(store, memory_pb2.Document(doc_id="p_alpha/memo", title="Memo", content_ref=MEMO))
        await ingest_document(store, memory_pb2.Document(doc_id="p_alpha/report", title="Brief", content_ref=REPORT))
        await ingest_document(
            store,
            memory_pb2.Document(doc_id="p_beta/memo", title="Other", content_ref=MEMO),
        )
        written = await link_documents(store, "p_alpha/report", complete_fn=fake)
        assert written == 1
        alpha = await store.query_subgraph(["p_alpha/root"], 4, [], 40)
        assert any(edge.kind == graph_pb2.EDGE_KIND_SUPPORTS for edge in alpha.edges)
        beta = await store.query_subgraph(["p_beta/root"], 4, [], 40)
        assert all(edge.kind != graph_pb2.EDGE_KIND_SUPPORTS for edge in beta.edges)

    asyncio.run(run())
