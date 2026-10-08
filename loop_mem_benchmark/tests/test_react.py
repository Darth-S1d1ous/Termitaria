import json

from memcompare.memory import MemoryHit
from memcompare.react import OfflineReActClient, ReActAgent, parse_react


class _ListMemory:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def add(self, text, metadata=None) -> None:
        return None

    def clear(self) -> None:
        return None

    def retrieve(self, query: str, k: int = 5):
        self.queries.append(query)
        return [MemoryHit(chunk_id="D1:3", text="Caroline went to the support group.", score=1.0)][:k]


def test_parse_final_answer_and_tool_call():
    parsed = parse_react("Thought: enough.\nFinal Answer: 7 May 2023")
    assert parsed.final_answer == "7 May 2023"
    call = parse_react(
        'Thought: look it up.\nAction: search_memory\nAction Input: {"query": "When did Caroline go?"}'
    )
    assert call.action == "search_memory"
    assert call.action_input == {"query": "When did Caroline go?"}


def test_offline_react_searches_then_answers_from_memory():
    memory = _ListMemory()
    agent = ReActAgent(memory, OfflineReActClient(), k=5, max_steps=4)
    result = agent.run("When did Caroline go to the LGBTQ support group?")
    assert memory.queries == ["When did Caroline go to the LGBTQ support group?"]
    assert result.steps[0].hits[0].chunk_id == "D1:3"
    assert "support group" in result.answer
    blob = json.dumps([message["content"] for message in result.raw_messages])
    assert "Observation:" in blob
