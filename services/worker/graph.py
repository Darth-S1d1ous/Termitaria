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
from .skills import catalog, load_skills
from .tools import Toolbox, load_project, make_toolbox, mention_delegates, parse_tool_calls, visible_text

EmitDelta = Callable[[task_pb2.TaskDelta], Awaitable[None]]

# role / rules / principle 构成 system prompt（Poincaré 球心侧的上位记忆）
_ROLE_KINDS = (
    memory_pb2.MEMORY_KIND_ROLE,
    memory_pb2.MEMORY_KIND_RULE,
    memory_pb2.MEMORY_KIND_PRINCIPLE,
)

# Memory 召回未接时的 demo 角色。召回结果优先于这里。
_MAX_TOOL_ROUNDS = 6
_WEEKDAYS = "一二三四五六日"

_FALLBACK_ROLES = {
    "ideator": (
        "你是 ideator。你提出并辩护研究假设，面对质疑时补证据或收窄主张。"
        "用中文，每次只回复两三句。"
    ),
    "reviewer": (
        "你是 reviewer。你挑剔地评审对方的假设：找漏洞、要证据、不轻易同意。"
        "用中文，每次只回复两三句。"
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
        skills = load_skills(root / "skills")
        rules = load_rules(root / "rules")
        steps = (project or {}).get("steps") or []
        roster = [
            {"id": item.get("id") or "", "display_name": item.get("display_name") or "", "kind": item.get("kind") or ""}
            for item in (project or {}).get("agents") or []
            if item.get("id") and item.get("id") != task.agent_id
        ]
        toolbox = make_toolbox(
            agent=agent,
            project_id=task.swarm_id,
            steps=steps,
            skills=skills,
            mcp_tools=load_mcp_tools(root / "mcp.json"),
            gateway=gateway,
        )
        held["tools"] = toolbox
        held["roster"] = roster
        held["kind"] = agent.get("kind") or ""
        role_lines = [it.text for it in task.context.recalled if it.kind in _ROLE_KINDS]
        role = "\n".join(role_lines) or _FALLBACK_ROLES.get(task.agent_id) or (
            f"You are agent '{task.agent_id}' in a Termitaria swarm. "
            "(stub system prompt：MemoryService 未接，role/rules 为空)"
        )
        system = _system(role, rules, skills, toolbox, roster, steps)
        window = [(_sender(m), _first_text(m)) for m in task.context.window]
        return {"system": system, "window": window}

    async def reason(state: WorkerState) -> dict:
        task = state["task"]
        toolbox: Toolbox = held.get("tools") or Toolbox([])
        window = list(state["window"])
        usage = None
        text = ""
        used_tools = False
        schemas = toolbox.schemas()
        declared = _declared_tools(schemas)
        for round_index in range(_MAX_TOOL_ROUNDS):
            if _deadline_exceeded(task.budget):
                return _cancelled(state.get("seq", 0), usage)
            # 最后一轮不再提供函数，逼模型用已经拿到的结果写答复。
            offer = None if used_tools and round_index == _MAX_TOOL_ROUNDS - 1 else schemas
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
            if not calls and round_index == 0 and held.get("kind") == "ORCHESTRATOR":
                # 模型如果还是只在正文里写 @，这里补一次真正的派活。
                calls = mention_delegates(text, held.get("roster") or [])
            if not calls:
                text = text or "推理被截断了，没有正文。"
                break
            used_tools = True
            notes: list[str] = []
            for call in calls:
                args = dict(call.arguments)
                if call.name in {"delegate", "ledger"} and notes:
                    prior = str(args.get("context") or "")
                    args["context"] = (prior + "\n\n" + "\n\n".join(notes)).strip()
                result = await toolbox.run(call.name, args)
                notes.append(f"{call.name} → {result}")
            window.append(("", text))
            window.append(("tool", "\n\n".join(notes)))
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
            text = text or "已根据工具结果结束。"
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


def _system(role: str, rules: str, skills, toolbox: Toolbox, roster: list[dict], steps: list) -> str:
    parts = [role, _today()]
    if rules:
        parts.append("规则：\n" + rules)
    listed = catalog(skills)
    if listed:
        parts.append(listed)
    if roster:
        lines = ["同事（派活用 delegate，coworker 填 id）："]
        for person in roster:
            lines.append(f"- {person['id']}（{person['display_name']}）")
        parts.append("\n".join(lines))
    if steps:
        lines = ["项目进度，做完调用 plan："]
        for step in steps:
            lines.append(f"- {step.get('label') or step.get('id')} [{step.get('state')}]")
        parts.append("\n".join(lines))
    guide = toolbox.instructions()
    if guide:
        parts.append(guide)
    return "\n\n".join(part for part in parts if part)


def _agent_of(project: dict | None, agent_id: str) -> dict:
    for item in (project or {}).get("agents") or []:
        if item.get("id") == agent_id:
            return item
    return {"id": agent_id, "kind": "WORKER", "tools": []}


def _today() -> str:
    now = datetime.now().astimezone()
    return (
        f"今天是 {now:%Y-%m-%d}，星期{_WEEKDAYS[now.weekday()]}。"
        "用户说的「上周」「最近」按这一天计算。"
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
