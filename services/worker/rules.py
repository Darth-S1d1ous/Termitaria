"""常驻规则。短文件，每次都放进 system prompt，不需要模型再去加载。"""
from __future__ import annotations

from pathlib import Path


def load_rules(directory: Path) -> str:
    if not directory.is_dir():
        return ""
    parts: list[str] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)
