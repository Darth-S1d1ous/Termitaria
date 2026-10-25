"""跨文件的主张边由模型命名。结构边 CITES 不从模型结果里进来。"""
from __future__ import annotations

import asyncio
import json

import services
from termitaria.graph.v1 import graph_pb2
from termitaria.memory.v1 import memory_pb2

from services.graph.falkor_store import _edge
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
        ("p/report/c1", "p/memo/c0", "SUPPORTS", "")
    ]


def test_parse_relations_keeps_an_open_label_and_its_fact():
    docs = {"p/memo/c0": "p/memo", "p/report/c1": "p/report"}
    raw = json.dumps([
        {
            "from": "p/report/c1",
            "to": "p/memo/c0",
            "relation": "priced at",
            "fact": "The brief prices the memo's wire at 4.80 dollars per kilogram.",
        },
        {"from": "p/report/c1", "to": "p/memo/c0", "relation": "CITES", "fact": "no"},
        {"from": "p/report/c1", "to": "p/memo/c0", "relation": "RELATES", "fact": "too vague"},
    ])
    assert parse_relations(raw, docs, {"p/report/c1"}) == [
        (
            "p/report/c1",
            "p/memo/c0",
            "PRICED_AT",
            "The brief prices the memo's wire at 4.80 dollars per kilogram.",
        )
    ]


def test_link_documents_writes_the_models_relation_between_files():
    store = InMemoryGraph()

    def fake(prompt: str) -> str:
        assert "p_alpha/memo/c0" in prompt
        assert "p_beta" not in prompt
        return json.dumps([
            {
                "from": "p_alpha/report/c0",
                "to": "p_alpha/memo/c0",
                "relation": "PRICED_AT",
                "fact": "The brief prices the memo's wire at 4.80 dollars per kilogram.",
            },
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
        priced = [edge for edge in alpha.edges if "relation" in edge.props and edge.props["relation"] == "PRICED_AT"]
        assert len(priced) == 1
        assert priced[0].kind == graph_pb2.EDGE_KIND_RELATES
        assert "4.80" in priced[0].props["fact"]
        beta = await store.query_subgraph(["p_beta/root"], 4, [], 40)
        assert all(edge.kind != graph_pb2.EDGE_KIND_RELATES for edge in beta.edges)

    asyncio.run(run())


def test_falkor_edge_row_keeps_relation_and_fact():
    edge = _edge(["e1", "p/a", "p/b", graph_pb2.EDGE_KIND_RELATES, "PRICED_AT", "The brief prices the wire at 4.80."])
    assert edge.props["relation"] == "PRICED_AT"
    assert edge.props["fact"] == "The brief prices the wire at 4.80."
