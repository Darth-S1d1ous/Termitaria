from memcompare.graph_memory import GraphMemory


def test_graph_retrieves_shared_entity_and_one_hop_neighbor():
    memory = GraphMemory()
    memory.add(
        "[1:56 pm on 8 May, 2023] Caroline: I went to a LGBTQ support group yesterday and it was so powerful.",
        {"chunk_id": "D1:3", "speaker": "Caroline"},
    )
    memory.add(
        "[1:56 pm on 8 May, 2023] Melanie: I painted a sunrise.",
        {"chunk_id": "D1:12", "speaker": "Melanie"},
    )
    hits = memory.retrieve("When did Caroline go to the LGBTQ support group?", k=2)
    assert hits, "expected at least one graph hit"
    assert hits[0].chunk_id == "D1:3"
    assert "D1:3" in hits[0].detail["direct_entities"] or hits[0].detail["direct_entities"]

    memory.clear()
    memory.add("Caroline adopted Luna.", {"chunk_id": "A", "speaker": "Caroline"})
    memory.add("Luna loves tuna.", {"chunk_id": "B", "speaker": "Melanie"})
    hits = memory.retrieve("Caroline", k=5)
    ids = [hit.chunk_id for hit in hits]
    assert "A" in ids
    assert "B" in ids
    hop = next(hit for hit in hits if hit.chunk_id == "B")
    assert hop.detail["hop_entities"]
    stats = memory.stats
    assert stats["utterances"] == 2
    assert stats["relations"] >= 1
    assert stats["entities"] >= 2
