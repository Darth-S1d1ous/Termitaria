"""从 SwarmSpec YAML 读出项目用得到的 agent 名册。边、拓扑、记忆策略留给控制面。"""
from __future__ import annotations

from pathlib import Path

import yaml

from .types import Agent, Project


def load_swarm(path: Path) -> Project:
    data = yaml.safe_load(path.read_text())
    agents = [
        Agent(
            id=raw["id"],
            display_name=raw.get("displayName") or raw["id"],
            kind=raw.get("kind") or "WORKER",
            role_prompt=str(raw["rolePrompt"]).strip(),
        )
        for raw in data["agents"]
    ]
    name = str(data["name"])
    return Project(
        id=name,
        name=name.replace("-", " ").title(),
        goal=str(data["goal"]).strip(),
        agents=agents,
    )
