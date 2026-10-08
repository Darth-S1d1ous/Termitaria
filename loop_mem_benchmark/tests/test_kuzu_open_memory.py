from memcompare.kuzu_open_memory import KuzuOpenMemory


def test_kuzu_memory_returns_the_turn_that_shares_the_question_words():
    memory = KuzuOpenMemory(candidate_window=20)
    try:
        memory.add(
            "[1:56 pm on 8 May, 2023] Caroline: I went to a LGBTQ support group yesterday and it was so powerful.",
            {"chunk_id": "D1:3", "speaker": "Caroline"},
        )
        memory.add(
            "[1:56 pm on 8 May, 2023] Melanie: I painted a sunrise.",
            {"chunk_id": "D1:12", "speaker": "Melanie"},
        )
        hits = memory.retrieve("When did Caroline go to the LGBTQ support group?", k=2)
        assert hits
        assert hits[0].chunk_id == "D1:3"
        assert "support group" in hits[0].text
        assert memory.stats["memories"] == 2

        memory.clear()
        assert memory.retrieve("When did Caroline go to the LGBTQ support group?", k=2) == []
        assert memory.stats["memories"] == 0
    finally:
        memory._close()
