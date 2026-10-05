from memcompare.locomo import CATEGORY_NAMES, load_locomo


def test_primary_slice_is_the_full_memory_qa_set():
    conversations = load_locomo()
    assert [convo.sample_id for convo in conversations] == [
        "conv-26",
        "conv-30",
        "conv-41",
        "conv-42",
        "conv-43",
        "conv-44",
        "conv-47",
        "conv-48",
        "conv-49",
        "conv-50",
    ]
    questions = [qa for convo in conversations for qa in convo.questions]
    assert len(questions) == 1444
    counts = {name: 0 for name in ("multi-hop", "temporal", "single-hop")}
    for qa in questions:
        assert qa.evidence
        assert qa.category in {1, 2, 4}
        counts[CATEGORY_NAMES[qa.category]] += 1
        assert any(chunk.chunk_id == qa.evidence[0] for chunk in next(c for c in conversations if c.sample_id == qa.sample_id).chunks)
    assert counts == {"multi-hop": 282, "temporal": 321, "single-hop": 841}
