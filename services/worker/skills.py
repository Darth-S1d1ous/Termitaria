"""技能目录。做法对齐 CrewAI：提示里只放名字和一句话，全文等 load_skill 再给。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def load_skills(directory: Path) -> list[Skill]:
    if not directory.is_dir():
        return []
    found: list[Skill] = []
    for path in sorted(directory.glob("*.md")):
        meta, body = _split(path.read_text(encoding="utf-8"))
        name = str(meta.get("name") or path.stem).strip()
        description = str(meta.get("description") or "").strip()
        if not description:
            description = body.splitlines()[0].strip() if body else name
        if name and body:
            found.append(Skill(name=name, description=description, body=body))
    return found


def catalog(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = [
        "These skills are not expanded yet. Most requests do not need them. When you do, call load_skill with name set to one of the names below.",
        "Do not follow a skill's full text until you have loaded it.",
    ]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
    return "\n".join(lines)


def _split(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text.strip()
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text.strip()
    meta = yaml.safe_load(text[3:end]) or {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[end + 4 :].strip()
