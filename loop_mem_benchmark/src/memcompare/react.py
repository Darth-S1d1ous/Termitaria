"""One ReAct loop. Memory is only reachable through the search_memory tool."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from memcompare.memory import Memory, MemoryHit

SYSTEM_PROMPT = """You answer questions about earlier conversations by searching memory first.
You have one tool:
- search_memory: retrieve the top {k} stored dialogue turns for a query.

When you need evidence, reply in exactly this form:
Thought: <why you are searching>
Action: search_memory
Action Input: {{"query": "<search text>"}}

When you can answer, reply in exactly this form:
Thought: <why this answers the question>
Final Answer: <short answer>

Do not invent facts that are not in an Observation.
"""


class ModelClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Return the next assistant message."""


@dataclass
class RetrievalStep:
    query: str
    hits: list[MemoryHit]


@dataclass
class AgentResult:
    answer: str
    steps: list[RetrievalStep] = field(default_factory=list)
    raw_messages: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ParsedStep:
    final_answer: str | None = None
    action: str | None = None
    action_input: dict | None = None


_ACTION_RE = re.compile(r"^Action:\s*([A-Za-z_][\w]*)\s*$", re.M)
_INPUT_RE = re.compile(r"^Action Input:\s*(\{.*\})\s*$", re.M | re.S)


def parse_react(text: str) -> ParsedStep:
    if "Final Answer:" in text:
        answer = text.split("Final Answer:", 1)[1].strip()
        return ParsedStep(final_answer=answer)
    action_match = _ACTION_RE.search(text)
    input_match = _INPUT_RE.search(text)
    action = action_match.group(1) if action_match else None
    payload = None
    if input_match:
        try:
            payload = json.loads(input_match.group(1))
        except json.JSONDecodeError:
            payload = None
    return ParsedStep(action=action, action_input=payload if isinstance(payload, dict) else {})


def format_hits(hits: list[MemoryHit]) -> str:
    if not hits:
        return "(no memories retrieved)"
    lines = []
    for i, hit in enumerate(hits, start=1):
        lines.append(f"[{i}] ({hit.chunk_id}) {hit.text}")
    return "\n".join(lines)


class ReActAgent:
    def __init__(self, memory: Memory, client: ModelClient, k: int = 5, max_steps: int = 4) -> None:
        self.memory = memory
        self.client = client
        self.k = k
        self.max_steps = max_steps

    def run(self, question: str) -> AgentResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(k=self.k)},
            {"role": "user", "content": question},
        ]
        steps: list[RetrievalStep] = []
        for _ in range(self.max_steps):
            raw = self.client.complete(messages)
            messages.append({"role": "assistant", "content": raw})
            parsed = parse_react(raw)
            if parsed.final_answer is not None:
                return AgentResult(parsed.final_answer, steps, messages)
            if parsed.action == "search_memory":
                query = str((parsed.action_input or {}).get("query") or question)
                hits = self.memory.retrieve(query, self.k)
                steps.append(RetrievalStep(query=query, hits=hits))
                messages.append({"role": "user", "content": "Observation:\n" + format_hits(hits)})
                continue
            messages.append(
                {
                    "role": "user",
                    "content": "Observation: Unknown action. Use search_memory or Final Answer.",
                }
            )
        return AgentResult("", steps, messages)


class OfflineReActClient:
    """Deterministic policy used when no LLM API key is set.

    It searches once with the original question, then copies the top memory
    text as the final answer. Retrieval metrics use that search call. The
    final answer is extractive and is not an LLM judgment.
    """

    name = "offline"

    def complete(self, messages: list[dict[str, str]]) -> str:
        question = next(m["content"] for m in messages if m["role"] == "user")
        seen = any(m["role"] == "user" and m["content"].startswith("Observation:") for m in messages)
        if not seen:
            payload = json.dumps({"query": question}, ensure_ascii=False)
            return (
                "Thought: The answer depends on earlier sessions, so I will search memory.\n"
                "Action: search_memory\n"
                f"Action Input: {payload}"
            )
        observation = next(
            m["content"] for m in reversed(messages) if m["content"].startswith("Observation:")
        )
        answer = _top_observation_text(observation)
        return (
            "Thought: I will answer from the highest-ranked memory.\n"
            f"Final Answer: {answer}"
        )


def _top_observation_text(observation: str) -> str:
    for line in observation.splitlines():
        if line.startswith("[1]"):
            # "[1] (D1:3) text..."
            rest = line.split(") ", 1)
            return rest[1].strip() if len(rest) == 2 else line
    return ""


class OpenAICompatibleClient:
    """Chat-completions client. Used only when an API key is present."""

    name = "openai-compatible"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, messages: list[dict[str, str]]) -> str:
        import urllib.error
        import urllib.request

        body = json.dumps(
            {"model": self.model, "messages": messages, "temperature": 0},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"LLM request failed: {exc.code} {detail}") from exc
        return payload["choices"][0]["message"]["content"]


def build_client() -> tuple[ModelClient, str]:
    """Return a client and a short label describing which path is active."""

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    if not api_key:
        return OfflineReActClient(), "offline"
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    return OpenAICompatibleClient(api_key, base, model), "llm"
