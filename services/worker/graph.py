"""LangGraph worker 图（架构 §4：reason → act → observe）。

工具调用收在 reason 这一轮里：模型吐 JSON，就地执行，结果写回窗口，再问一次。
最终给用户的那句话才流式发出。这样负责人可以在同一轮里派活、收结果，不用把轮次交还给人。
记忆不在图里读：TaskContext.recalled / graph_node_ids 由 Go 侧在 dispatch 前预取。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from termitaria.common.v1 import common_pb2
from termitaria.memory.v1 import memory_pb2
from termitaria.session.v1 import session_pb2
from termitaria.task.v1 import task_pb2

from .calls import Call, extract_tool_call
from .deepseek_dsml import recover_dsml
from .mcp import load_mcp_tools
from .model import Done, ModelPort
from .rules import load_rules
from .skills import load_skills
from .tools import Toolbox, load_project, make_toolbox, materials_text, mention_delegates, parse_tool_calls, visible_text

EmitDelta = Callable[[task_pb2.TaskDelta], Awaitable[None]]

# role / rules / principle 放进 system prompt。episode 单独记。
_ROLE_KINDS = (
    memory_pb2.MEMORY_KIND_ROLE,
    memory_pb2.MEMORY_KIND_RULE,
    memory_pb2.MEMORY_KIND_PRINCIPLE,
)
_MAX_EPISODES = 5

# Memory 召回未接时的 demo 角色。召回结果优先于这里。
_MAX_TOOL_ROUNDS = 6
_MAX_ORCH_ROUNDS = 8
_OPEN_NUDGE = (
    "The project is still open. Call delegate, advance, write_report, write_deck, or ask_user. "
    "A plain reply does not finish the turn."
)
_DATA_TOOLS = {"web_search", "fetch_url", "edgar", "utc_time"}
_DATA_NUDGE = (
    "You have not called a data tool. The skill text is already in your instructions. "
    "Call utc_time, fetch_url, web_search, or edgar before you answer. "
    "A gap is allowed only after a tool result says the source is missing."
)
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

_FALLBACK_ROLES = {
    "ideator": (
        "You are the ideator. You propose and defend a research hypothesis. "
        "When challenged, add evidence or narrow the claim. Answer in two or three sentences."
    ),
    "reviewer": (
        "You are the reviewer. You review the hypothesis skeptically: find gaps, demand evidence, "
        "and do not agree easily. Answer in two or three sentences."
    ),
}


class WorkerState(TypedDict, total=False):
    task: task_pb2.Task
    system: str
    window: list[tuple[str, str]]  # (sender_id, text)
    text: str
    usage: task_pb2.TaskUsage
    error: common_pb2.Error
    cancelled: bool
    seq: int  # 已发出的 delta 序号；text chunk 与收尾帧共用一条序列


def build_graph(model: ModelPort, emit: EmitDelta):
    """构造编译后的图。emit 由 runner 注入（发布到 task.delta_subject）。"""

    held: dict = {}

    async def build_prompt(state: WorkerState) -> dict:
        task = state["task"]
        root = Path(os.environ.get("TERMITARIA_ROOT", os.getcwd()))
        gateway = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8787").rstrip("/")
        project = load_project(gateway, task.swarm_id)
        agent = _agent_of(project, task.agent_id)
        all_skills = load_skills(root / "skills")
        rules = load_rules(root / "rules")
        steps = (project or {}).get("steps") or []
        own = {name for name in (agent.get("skills") or []) if name}
        visible = set(own)
        if (agent.get("kind") or "") == "ORCHESTRATOR":
            for item in (project or {}).get("agents") or []:
                visible.update(name for name in (item.get("skills") or []) if name)
        catalog_skills = [skill for skill in all_skills if skill.name in visible]
        loadable = [skill for skill in all_skills if skill.name in own]
        roster = []
        for item in (project or {}).get("agents") or []:
            if not item.get("id") or item.get("id") == task.agent_id:
                continue
            roster.append({
                "id": item.get("id") or "",
                "display_name": item.get("display_name") or "",
                "kind": item.get("kind") or "",
                "skills": [name for name in (item.get("skills") or []) if name],
            })
        seeds = (project or {}).get("seeds") or []
        toolbox = make_toolbox(
            agent=agent,
            project_id=task.swarm_id,
            steps=steps,
            skills=loadable,
            mcp_tools=load_mcp_tools(root / "mcp.json"),
            gateway=gateway,
            seeds=seeds,
        )
        held["tools"] = toolbox
        held["roster"] = roster
        held["kind"] = agent.get("kind") or ""
        held["steps"] = steps
        held["files"] = []
        held["asked"] = False
        role_lines = [it.text for it in task.context.recalled if it.kind in _ROLE_KINDS]
        role = "\n".join(role_lines) or _FALLBACK_ROLES.get(task.agent_id) or (
            f"You are agent '{task.agent_id}' in a Termitaria swarm. "
            "(stub system prompt: MemoryService is not connected, so role and rules are empty)"
        )
        facts = remembered_facts(task.context.recalled)
        if facts:
            role = f"{role}\n\n{facts}"
        knowledge = task.context.knowledge.strip()
        if knowledge:
            role = f"{role}\n\n{knowledge}"
        system = _system(role, rules, catalog_skills, toolbox, roster, steps, materials_text(seeds))
        window = [(_sender(m), _first_text(m)) for m in task.context.window]
        return {"system": system, "window": window}

    async def reason(state: WorkerState) -> dict:
        task = state["task"]
        toolbox: Toolbox = held.get("tools") or Toolbox([])
        window = list(state["window"])
        usage = None
        text = ""
        used_tools = False
        used_data = False
        schemas = toolbox.schemas()
        declared = _declared_tools(schemas)
        orch = held.get("kind") == "ORCHESTRATOR"
        limit = _MAX_ORCH_ROUNDS if orch else _MAX_TOOL_ROUNDS
        for round_index in range(limit):
            if _deadline_exceeded(task.budget):
                return _cancelled(state.get("seq", 0), usage)
            # 工人的最后一轮不再提供函数。负责人的函数要留到这一轮真正结束。
            offer = schemas
            if not orch and used_tools and round_index == limit - 1:
                offer = None
            try:
                text, calls, usage = await _complete(
                    model, state["system"], window, task, offer, declared
                )
            except _Budget:
                return _cancelled(state.get("seq", 0), usage)
            except Exception as exc:  # 模型/网络错误 → FAILED；不抛出（ack 语义见 consumer）
                return {
                    "error": common_pb2.Error(code="WORKER_ERROR", message=str(exc), retryable=False),
                    "seq": state.get("seq", 0),
                    "usage": usage,
                }
            if not calls and round_index == 0 and orch:
                # 模型如果还是只在正文里写 @，这里补一次真正的派活。
                calls = mention_delegates(text, held.get("roster") or [])
            if not calls and orch:
                if _orch_can_stop(held):
                    text = _with_files(text, held)
                    break
                if round_index == limit - 1:
                    text = _forced_ask(held, window)
                    break
                window.append(("", text))
                window.append(("tool", _OPEN_NUDGE))
                continue
            if (
                not calls
                and not orch
                and not used_data
                and declared & _DATA_TOOLS
                and round_index < 3
            ):
                window.append(("", text))
                window.append(("tool", _DATA_NUDGE))
                continue
            if not calls:
                text = text or "The reply was cut off before it had any text."
                break
            used_tools = True
            notes: list[str] = []
            asked = ""
            for call in calls:
                args = dict(call.arguments)
                if call.name in {"delegate", "ledger"} and notes:
                    prior = str(args.get("context") or "")
                    args["context"] = (prior + "\n\n" + "\n\n".join(notes)).strip()
                result = await toolbox.run(call.name, args)
                _remember_progress(held, call.name, result)
                if call.name in _DATA_TOOLS:
                    used_data = True
                if call.name == "ask_user" and result.startswith("What I already have:"):
                    asked = result
                notes.append(f"{call.name} → {result}")
            window.append(("", text))
            window.append(("tool", "\n\n".join(notes)))
            if asked:
                text = asked
                break
            if offer is None:
                # 收尾轮不再提供函数，但模型仍可能把调用写在正文里。执行完再要一句给人看的话。
                try:
                    follow, more, usage = await _complete(
                        model, state["system"], window, task, None, declared
                    )
                    if more:
                        extra: list[str] = []
                        for call in more:
                            result = await toolbox.run(call.name, dict(call.arguments))
                            extra.append(f"{call.name} → {result}")
                        window.append(("", follow))
                        window.append(("tool", "\n\n".join(extra)))
                        follow, _, usage = await _complete(
                            model, state["system"], window, task, None, declared
                        )
                except _Budget:
                    return _cancelled(state.get("seq", 0), usage)
                except Exception as exc:
                    return {
                        "error": common_pb2.Error(code="WORKER_ERROR", message=str(exc), retryable=False),
                        "seq": state.get("seq", 0),
                        "usage": usage,
                    }
                text = (follow or "").strip() or "\n\n".join(notes)
                break
        else:
            if orch and not _orch_can_stop(held):
                text = _forced_ask(held, window)
            elif orch:
                text = _with_files("The brief is ready.", held)
            else:
                text = text or "Stopped after the tool results."
        seq = state.get("seq", 0)
        for piece in _split_chunks(text, 6):
            if not piece:
                continue
            seq += 1
            await emit(task_pb2.TaskDelta(task_id=task.task_id, seq=seq, text=piece))
        out: dict = {"text": text, "seq": seq}
        if usage is not None:
            out["usage"] = usage
        return out

    async def finalize(state: WorkerState) -> dict:
        task = state["task"]
        seq = state.get("seq", 0) + 1
        err = state.get("error")
        usage = state.get("usage") or task_pb2.TaskUsage(model=task.model.model or "unknown")
        # 收尾帧二选一：error 优先于 usage（contracts §4：delta 的终态帧）。
        if err is not None and err.code:
            await emit(task_pb2.TaskDelta(task_id=task.task_id, seq=seq, error=err))
        else:
            await emit(task_pb2.TaskDelta(task_id=task.task_id, seq=seq, usage=usage))
        return {"seq": seq, "usage": usage}

    g = StateGraph(WorkerState)
    g.add_node("build_prompt", build_prompt)
    g.add_node("reason", reason)
    g.add_node("finalize", finalize)
    g.add_edge(START, "build_prompt")
    g.add_edge("build_prompt", "reason")
    g.add_edge("reason", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


class _Budget(Exception):
    pass


async def _complete(
    model: ModelPort,
    system: str,
    window: list[tuple[str, str]],
    task: task_pb2.Task,
    tools: list | None,
    declared: set[str],
):
    parts: list[str] = []
    calls: list[Call] = []
    usage = None
    async for ev in model.stream(
        system=system,
        window=window,
        model=task.model,
        max_tokens=task.budget.max_tokens,
        tools=tools or None,
    ):
        if _deadline_exceeded(task.budget):
            raise _Budget()
        if isinstance(ev, Done):
            usage = ev.usage
            continue
        if isinstance(ev, Call):
            calls.append(ev)
            continue
        parts.append(ev.text)
    # 原生 tool_calls 优先。没有时，先补 DeepSeek 漏在正文里的 DSML，再认 JSON。
    # DSML 补丁不看这一轮有没有把函数声明发给模型：最后一轮关掉函数后，模型仍可能把调用写在正文里。
    spoken = visible_text("".join(parts))
    if not calls:
        # 收尾轮没有函数声明时，调用仍可能写在正文里。这里照样执行，不把原文交给用户。
        spoken, dsml_calls = recover_dsml(spoken, declared)
        json_calls = parse_tool_calls(spoken)
        calls = [*dsml_calls, *json_calls]
        if json_calls:
            spoken = _without_json_calls(spoken)
    return spoken, calls, usage


def _without_json_calls(text: str) -> str:
    """去掉已经识别成调用的 JSON，留下给人看的句子。"""
    decoder = json.JSONDecoder()
    kept: list[str] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            kept.append(text[index:])
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            kept.append(text[index : start + 1])
            index = start + 1
            continue
        call = extract_tool_call(obj) if isinstance(obj, dict) else None
        if call is not None:
            kept.append(text[index:start])
            index = end
            continue
        kept.append(text[index : start + 1])
        index = start + 1
    cleaned = "".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _declared_tools(tools: list) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            names.add(str(function["name"]))
    return names


def _cancelled(seq: int, usage) -> dict:
    out: dict = {
        "seq": seq,
        "cancelled": True,
        "text": "",
        "error": common_pb2.Error(
            code="BUDGET_EXCEEDED", message="deadline_ms exceeded", retryable=False
        ),
    }
    if usage is not None:
        out["usage"] = usage
    return out


def remembered_facts(items, limit: int = _MAX_EPISODES) -> str:
    lines = []
    for item in items:
        if item.kind != memory_pb2.MEMORY_KIND_EPISODE:
            continue
        text = item.text.strip()
        if not text:
            continue
        lines.append(text)
        if len(lines) >= limit:
            break
    if not lines:
        return ""
    return "Remembered facts:\n" + "\n".join(f"- {line}" for line in lines)


def _system(role: str, rules: str, skills, toolbox: Toolbox, roster: list[dict], steps: list, materials: str = "") -> str:
    parts = [role, _today()]
    if materials:
        parts.append(materials)
    if rules:
        parts.append("Rules:\n" + rules)
    loadable = set()
    load_tool = toolbox.tools.get("load_skill") if toolbox else None
    if load_tool is not None:
        loadable = set((load_tool.parameters or {}).get("name", {}).get("enum") or [])
    own = [skill for skill in skills if skill.name in loadable]
    others = [skill for skill in skills if skill.name not in loadable]
    if own:
        lines = [
            "These skills are bound to you. Follow them by calling the tools they name. Do not stop to ask for the skill text.",
        ]
        for skill in own:
            lines.append(f"## {skill.name}\n{skill.description}\n\n{skill.body}")
        parts.append("\n\n".join(lines))
    if others:
        lines = ["Coworker skills. Name one in the delegate task. Do not call load_skill for a skill that is not yours."]
        for skill in others:
            lines.append(f"- {skill.name}: {skill.description}")
        parts.append("\n".join(lines))
    if roster:
        lines = ["Coworkers (delegate with coworker set to the id):"]
        for person in roster:
            bound = person.get("skills") or []
            extra = f" skills: {', '.join(bound)}" if bound else ""
            lines.append(f"- {person['id']}（{person['display_name']}）{extra}")
        parts.append("\n".join(lines))
    if steps:
        lines = ["Project progress. Call advance when a step is finished:"]
        for step in steps:
            lines.append(f"- {step.get('label') or step.get('id')} [{step.get('state')}]")
        parts.append("\n".join(lines))
    guide = toolbox.instructions()
    if guide:
        parts.append(guide)
    return "\n\n".join(part for part in parts if part)


def _orch_can_stop(held: dict) -> bool:
    if held.get("asked"):
        return True
    steps = held.get("steps") or []
    return bool(steps) and all(step.get("state") == "done" for step in steps)


def _remember_progress(held: dict, name: str, result: str) -> None:
    if name in {"advance", "plan"}:
        for line in result.splitlines():
            if line.startswith("steps: "):
                held["steps"] = _parse_steps(line[len("steps: "):])
    if name in {"write_report", "write_deck"} and result.startswith("Stored "):
        parts = result.split()
        if len(parts) >= 3:
            held.setdefault("files", []).append(parts[2])
    if name == "ask_user" and result.startswith("What I already have:"):
        held["asked"] = True


def _parse_steps(raw: str) -> list[dict]:
    steps = []
    for part in raw.split(";"):
        part = part.strip()
        if part.endswith("]") and " [" in part:
            label, state = part.rsplit(" [", 1)
            steps.append({"label": label, "state": state[:-1]})
    return steps


def _with_files(text: str, held: dict) -> str:
    missing = [name for name in held.get("files") or [] if name and name not in text]
    if not missing:
        return text
    lines = "\n".join(f"- {name}" for name in missing)
    return text.rstrip() + "\n\nFiles:\n" + lines


def _forced_ask(held: dict, window: list[tuple[str, str]]) -> str:
    notes = [
        text for sender, text in window
        if sender == "tool" and text and not text.startswith("The project is still open")
    ]
    known = notes[-1] if notes else "No tool result is in hand yet."
    if len(known) > 1200:
        known = known[:1200]
    active = "the open step"
    for step in held.get("steps") or []:
        if step.get("state") == "active":
            active = step.get("label") or active
            break
    return (
        f"What I already have:\n{known}\n\n"
        f"Question:\nThe step \"{active}\" is still open. "
        "Which source should I open before I write the brief?"
    )


def _agent_of(project: dict | None, agent_id: str) -> dict:
    for item in (project or {}).get("agents") or []:
        if item.get("id") == agent_id:
            return item
    return {"id": agent_id, "kind": "WORKER", "tools": []}


def _today() -> str:
    now = datetime.now().astimezone()
    return (
        f"Today is {now:%Y-%m-%d}, {_WEEKDAYS[now.weekday()]}. "
        "Interpret last week and recent from this date."
    )


def _split_chunks(text: str, n: int) -> list[str]:
    if not text:
        return []
    if n <= 1 or len(text) <= n:
        return [text]
    step = max(1, len(text) // n)
    return [text[i : i + step] for i in range(0, len(text), step)]


def _deadline_exceeded(budget: common_pb2.Budget) -> bool:
    # common.proto：deadline_ms 是绝对截止时间（epoch ms），0 = 不限。
    # 注意：schemas/swarmspec.schema.json 里 budget.deadlineMs 的注释是「相对毫秒数」——
    # 两侧语义不一致，应由 SwarmSpec loader 在入队时换算成绝对时间（已知契约缺口）。
    return bool(budget.deadline_ms) and time.time() * 1000 > budget.deadline_ms


def _sender(msg: session_pb2.Message) -> str:
    # proto 字段名 from 是 Python 关键字，只能 getattr。
    p = getattr(msg, "from")
    kind = p.WhichOneof("kind")
    return getattr(p, kind) if kind else "unknown"


def _first_text(msg: session_pb2.Message) -> str:
    for b in msg.content:
        if b.WhichOneof("block") == "text":
            return b.text
    return ""
