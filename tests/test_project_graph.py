"""一个 project 的文档和主张不会出现在另一个 project 的子图里。"""
from __future__ import annotations

import asyncio

import services
from termitaria.memory.v1 import memory_pb2

from services.graph.project_graph import claim_texts, ingest_document
from services.graph.store import InMemoryGraph


def test_claims_come_from_long_paragraphs():
    body = "short\n\nNorthwire sells low-carbon copper wire to data-center builders in Texas."
    assert claim_texts(body) == [
        "Northwire sells low-carbon copper wire to data-center builders in Texas."
    ]


def test_two_projects_do_not_share_nodes():
    store = InMemoryGraph()

    async def run() -> None:
        await ingest_document(
            store,
            memory_pb2.Document(
                doc_id="p_alpha/memo",
                title="Northwire memo",
                content_ref="Northwire asked 4.80 dollars per kilogram for low-carbon busbar wire.",
            ),
        )
        await ingest_document(
            store,
            memory_pb2.Document(
                doc_id="p_beta/memo",
                title="Other memo",
                content_ref="Helios quoted 9.10 dollars per kilogram for roof copper.",
                mime_type="application/link",
            ),
        )
        alpha = await store.query_subgraph(["p_alpha/root"], 2, [], 24)
        beta = await store.query_subgraph(["p_beta/root"], 2, [], 24)
        alpha_text = " ".join(node.props["text"] for node in alpha.nodes if "text" in node.props)
        beta_text = " ".join(node.props["text"] for node in beta.nodes if "text" in node.props)
        assert "4.80" in alpha_text
        assert "9.10" not in alpha_text
        assert "9.10" in beta_text
        assert "4.80" not in beta_text
        assert any(node.kind == 2 for node in beta.nodes)  # PAPER

    asyncio.run(run())
