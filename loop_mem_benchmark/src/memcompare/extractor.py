"""Offline entity and relation extractor.

Mem0 and Graphiti both ask an LLM to build the entity graph. This environment
has no LLM API key, so graph population uses spaCy (``en_core_web_sm``):
named entities, noun chunks, content-verb lemmas, and dependency SVO triples.
First-person subjects are grounded to the utterance speaker. Short person
names that are a unique prefix of a known speaker ("Mel" / "Melanie") collapse
to that speaker. The approximation is lexical, not a neural information
extractor, and it is the only ingestion step the Poincaré module does not share.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import spacy
from spacy.language import Language

from memcompare.textnorm import (
    LIGHT_VERBS,
    PRON_I,
    STOP_NORMS,
    normalize,
)

# NER labels that are too numeric to be useful graph nodes.
_SKIP_NER = {"CARDINAL", "ORDINAL", "PERCENT", "QUANTITY", "MONEY"}

_TYPE_RANK = {
    "PERSON": 0,
    "GPE": 1,
    "ORG": 2,
    "NORP": 3,
    "EVENT": 4,
    "FAC": 5,
    "PRODUCT": 6,
    "WORK_OF_ART": 7,
    "LAW": 8,
    "DATE": 9,
    "TIME": 10,
    "LOC": 11,
    "NOUN": 20,
    "VERB": 30,
}

_OBJ_DEPS = {"dobj", "obj", "attr", "oprd", "dative"}
_SUBJ_DEPS = {"nsubj", "nsubjpass"}


@dataclass
class Entity:
    norm: str
    name: str
    type: str


@dataclass
class Relation:
    subject: str
    predicate: str
    object: str


@dataclass
class Extraction:
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


def _alias_speaker(norm: str, speakers: set[str]) -> str:
    hits = [s for s in speakers if s.startswith(norm) and s != norm and len(norm) >= 3]
    if len(hits) == 1:
        return hits[0]
    return norm


class Extractor:
    def __init__(self, model: str = "en_core_web_sm") -> None:
        self.nlp: Language = spacy.load(model)

    def extract_many(
        self,
        texts: list[str],
        speakers: list[str | None],
        known_speakers: set[str] | None = None,
    ) -> list[Extraction]:
        speaker_norms = {
            normalize(s) for s in (known_speakers or set()) if s and normalize(s)
        }
        for speaker in speakers:
            if speaker and normalize(speaker):
                speaker_norms.add(normalize(speaker))
        docs = self.nlp.pipe(texts, batch_size=64)
        return [
            self._extract_doc(doc, speaker, speaker_norms)
            for doc, speaker in zip(docs, speakers, strict=True)
        ]

    def _extract_doc(self, doc, speaker: str | None, speaker_norms: set[str]) -> Extraction:
        speaker_norm = normalize(speaker) if speaker else ""
        spans: list[tuple[int, int, str]] = []
        found: dict[str, Entity] = {}

        def add(name: str, type_: str, start: int | None = None, end: int | None = None) -> None:
            norm = normalize(name)
            if not norm or len(norm) > 80 or norm in STOP_NORMS:
                return
            if len(norm) < 3 and type_ != "PERSON":
                return
            if type_ == "PERSON":
                norm = _alias_speaker(norm, speaker_norms)
            rank = _TYPE_RANK.get(type_, 25)
            current = found.get(norm)
            if current is None or rank < _TYPE_RANK.get(current.type, 25):
                found[norm] = Entity(norm, name.strip()[:120], type_)
            if start is not None and end is not None:
                spans.append((start, end, norm))

        if speaker_norm:
            add(speaker or speaker_norm, "PERSON")

        for ent in doc.ents:
            if ent.label_ in _SKIP_NER:
                continue
            add(ent.text, ent.label_, ent.start, ent.end)

        for chunk in doc.noun_chunks:
            if chunk.root.pos_ not in {"NOUN", "PROPN"}:
                continue
            add(chunk.text, "NOUN" if chunk.root.pos_ == "NOUN" else "PERSON", chunk.start, chunk.end)

        for token in doc:
            if token.pos_ != "VERB":
                continue
            lemma = token.lemma_.lower()
            if lemma in LIGHT_VERBS:
                continue
            add(lemma, "VERB", token.i, token.i + 1)

        cover: dict[int, tuple[int, str]] = {}
        for start, end, norm in spans:
            length = end - start
            for i in range(start, end):
                prev = cover.get(i)
                if prev is None or length > prev[0]:
                    cover[i] = (length, norm)

        def ent_at(token) -> str | None:
            if token.lower_ in PRON_I and speaker_norm:
                return speaker_norm
            rec = cover.get(token.i)
            return rec[1] if rec else None

        relations: list[Relation] = []
        seen_rel: set[tuple[str, str, str]] = set()
        for token in doc:
            if token.dep_ not in _SUBJ_DEPS:
                continue
            verb = token.head
            if verb.pos_ not in {"VERB", "AUX"}:
                continue
            subj = ent_at(token)
            if not subj or subj not in found:
                continue
            lemma = verb.lemma_.lower()
            if lemma in LIGHT_VERBS:
                continue

            def push(obj: str | None, predicate: str) -> None:
                if not obj or obj == subj or obj not in found:
                    return
                pred = normalize(predicate)
                if not pred or pred in LIGHT_VERBS:
                    return
                key = (subj, pred, obj)
                if key in seen_rel:
                    return
                seen_rel.add(key)
                relations.append(Relation(subj, pred, obj))

            for child in verb.children:
                if child.dep_ in _OBJ_DEPS:
                    push(ent_at(child), lemma)
                elif child.dep_ == "prep":
                    for gc in child.children:
                        if gc.dep_ == "pobj":
                            push(ent_at(gc), f"{lemma} {child.lemma_.lower()}")

        return Extraction(list(found.values()), relations[:24])
