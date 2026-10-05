"""Shared normalization for entity names and answer strings."""

from __future__ import annotations

import re
import string

LEADING_DET = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "my",
    "your",
    "his",
    "her",
    "our",
    "their",
    "its",
}

# Pronouns and generic nouns that would glue the graph together without meaning.
STOP_NORMS = {
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "myself",
    "yourself",
    "himself",
    "herself",
    "itself",
    "ourselves",
    "themselves",
    "someone",
    "somebody",
    "something",
    "anyone",
    "anything",
    "everyone",
    "everybody",
    "everything",
    "people",
    "person",
    "thing",
    "things",
    "stuff",
    "way",
    "ways",
    "lot",
    "lots",
    "bit",
    "kind",
    "kinds",
    "part",
    "parts",
    "one",
    "ones",
    "time",
    "times",
    "day",
    "days",
    "today",
    "yesterday",
    "tomorrow",
    "what",
    "when",
    "where",
    "who",
    "why",
    "how",
    "which",
}

LIGHT_VERBS = {
    "be",
    "am",
    "is",
    "are",
    "was",
    "were",
    "been",
    "being",
    "do",
    "does",
    "did",
    "done",
    "doing",
    "have",
    "has",
    "had",
    "having",
    "will",
    "would",
    "can",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
}

PRON_I = {"i", "me", "my", "mine", "myself"}

_PUNCT = str.maketrans({c: " " for c in string.punctuation})
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation and leading determiners."""

    text = text.lower().replace("&", " and ")
    text = text.translate(_PUNCT)
    text = _SPACE.sub(" ", text).strip()
    toks = text.split()
    while toks and toks[0] in LEADING_DET:
        toks = toks[1:]
    return " ".join(toks)


def ngrams(text: str, max_n: int = 5) -> set[str]:
    toks = normalize(text).split()
    grams: set[str] = set()
    for n in range(1, max_n + 1):
        for i in range(len(toks) - n + 1):
            grams.add(" ".join(toks[i : i + n]))
    return grams
