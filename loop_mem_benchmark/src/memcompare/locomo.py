"""LoCoMo loader.

Category ids follow ``task_eval/evaluation.py`` in snap-research/locomo, which
does not match the prose order in the paper:

    1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop, 5 adversarial

The primary slice is categories 1, 2, and 4: every one of those questions has
dialogue evidence, and the answer depends on turns from the long multi-session
conversation rather than on a single isolated sentence presented with the question.
Open-domain items need knowledge outside the chat. Adversarial items are
unanswerable ("not in the conversation") and are excluded from the score.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DATASET_REVISION = "cbfbc1dba6bc53d00625212a0f22d55ffee7c1fc"
DATASET_URL = (
    "https://raw.githubusercontent.com/snap-research/locomo/"
    f"{DATASET_REVISION}/data/locomo10.json"
)
DATASET_SHA256 = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}
PRIMARY_CATEGORIES = (1, 2, 4)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    speaker: str
    session: int
    date: str


@dataclass(frozen=True)
class QAItem:
    sample_id: str
    qa_index: int
    question: str
    answer: str | None
    category: int
    evidence: tuple[str, ...]

    @property
    def qid(self) -> str:
        return f"{self.sample_id}:{self.qa_index}"


@dataclass
class Conversation:
    sample_id: str
    chunks: list[Chunk]
    questions: list[QAItem]


def default_data_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "locomo10.json"


def ensure_dataset(path: Path | None = None) -> Path:
    path = path or default_data_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(DATASET_URL, path)
    return path


def load_locomo(
    path: Path | None = None,
    categories: tuple[int, ...] = PRIMARY_CATEGORIES,
    max_conversations: int | None = None,
) -> list[Conversation]:
    path = ensure_dataset(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if max_conversations is not None:
        raw = raw[:max_conversations]
    wanted = set(categories)
    conversations: list[Conversation] = []
    for sample in raw:
        conversations.append(
            Conversation(
                sample_id=sample["sample_id"],
                chunks=_chunks(sample["conversation"]),
                questions=_questions(sample, wanted),
            )
        )
    return conversations


def _chunks(conversation: dict) -> list[Chunk]:
    sessions = []
    for key, value in conversation.items():
        if key.startswith("session_") and not key.endswith("_date_time") and isinstance(value, list):
            session_no = int(key.split("_", 1)[1])
            sessions.append((session_no, value))
    sessions.sort(key=lambda item: item[0])
    chunks: list[Chunk] = []
    for session_no, turns in sessions:
        date = str(conversation.get(f"session_{session_no}_date_time") or "")
        for turn in turns:
            text = turn.get("text") or ""
            caption = turn.get("blip_caption")
            if caption:
                text = f"{text} (image: {caption})"
            rendered = f"[{date}] {turn.get('speaker', '')}: {text}".strip()
            chunks.append(
                Chunk(
                    chunk_id=turn["dia_id"],
                    text=rendered,
                    speaker=str(turn.get("speaker") or ""),
                    session=session_no,
                    date=date,
                )
            )
    return chunks


_EVIDENCE_SPLIT = re.compile(r"[;|]")
_DIA_FIX = re.compile(r"^D:(\d+):(\d+)$")
_DIA_ZERO = re.compile(r"^D(\d+):0*(\d+)$")


def _clean_evidence(raw: list, valid_ids: set[str]) -> tuple[str, ...]:
    """Repair a handful of malformed LoCoMo evidence strings, then drop unknown ids.

    The release contains values such as ``D8:6; D9:17`` (two ids in one string),
    ``D:11:26`` (an extra colon), ``D30:05`` (a leading zero), and ids that do
    not exist (``D``, ``D10:19``). Questions with no remaining valid id are skipped.
    """

    cleaned: list[str] = []
    for item in raw or []:
        for part in _EVIDENCE_SPLIT.split(str(item)):
            token = part.strip()
            if not token:
                continue
            fixed = _DIA_FIX.sub(r"D\1:\2", token)
            zero = _DIA_ZERO.fullmatch(fixed)
            if zero:
                fixed = f"D{int(zero.group(1))}:{int(zero.group(2))}"
            if fixed in valid_ids and fixed not in cleaned:
                cleaned.append(fixed)
    return tuple(cleaned)


def _questions(sample: dict, categories: set[int]) -> list[QAItem]:
    valid_ids = {chunk.chunk_id for chunk in _chunks(sample["conversation"])}
    items: list[QAItem] = []
    for index, qa in enumerate(sample["qa"]):
        category = int(qa["category"])
        if category not in categories:
            continue
        evidence = _clean_evidence(qa.get("evidence") or [], valid_ids)
        if not evidence:
            continue
        answer = qa.get("answer")
        items.append(
            QAItem(
                sample_id=sample["sample_id"],
                qa_index=index,
                question=qa["question"],
                answer=str(answer) if answer else None,
                category=category,
                evidence=evidence,
            )
        )
    return items
