"""LangGraph worker 图（架构 §4：reason → act → observe）。

工具调用收在 reason 这一轮里：模型吐 JSON，就地执行，结果写回窗口，再问一次。
负责人的 delegate 也在这一轮里把同事跑完。同事开始前读取自己的记忆和这个项目的知识图。
最终给用户的那句话才流式发出。
用户这条 task 的记忆和图仍由 Go dispatcher 在入队前预取。
"""
from __future__ import annotations

import asyncio
import json
import logging
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
from . import envelope
from .tools import (
    Toolbox,
    collect_urls,
    fresh_uploads,
    load_project,
    make_toolbox,
    materials_text,
    mention_delegates,
    parse_tool_calls,
    upload_notice,
    visible_text,
)

log = logging.getLogger(__name__)

EmitDelta = Callable[[task_pb2.TaskDelta], Awaitable[None]]

# role / rules / principle 放进 system prompt。episode 单独记。
_ROLE_KINDS = (
    memory_pb2.MEMORY_KIND_ROLE,
    memory_pb2.MEMORY_KIND_RULE,
    memory_pb2.MEMORY_KIND_PRINCIPLE,
)
_MAX_EPISODES = 5

# Memory 召回未接时的 demo 角色。召回结果优先于这里。
# contracts §4：同一个图，最多 20 轮工具。同事在这一轮里跑完，也用这个上限。
_MAX_TOOL_ROUNDS = 20
_MAX_ORCH_ROUNDS = 20
_OPEN_NUDGE = (
    "The project is still open. Call delegate, advance, write_report, write_deck, or ask_user. "
    "A plain reply does not finish the turn."
)
_DATA_TOOLS = {"web_search", "fetch_url", "edgar", "utc_time", "kg"}
_DATA_NUDGE = (
    "You have not called a data tool. The skill text is already in your instructions. "
    "Call kg, utc_time, fetch_url, web_search, or edgar before you answer. "
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


def build_graph(model: ModelPort, emit: EmitDelta, nc=None):
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
                "role": item.get("role_prompt") or "",
                "skills": [name for name in (item.get("skills") or []) if name],
            })
        seeds = (project or {}).get("seeds") or []
        sources: set[str] = set()

        async def delegate_run(coworker: str, task_text: str, context: str) -> str:
            fresh = load_project(gateway, task.swarm_id) or project
            return await run_coworker(
                model, nc, task, fresh, gateway, sources, coworker, task_text, context
            )

        toolbox = make_toolbox(
            agent=agent,
            project_id=task.swarm_id,
            steps=steps,
            skills=loadable,
            mcp_tools=load_mcp_tools(root / "mcp.json"),
            gateway=gateway,
            seeds=seeds,
            deliverable=(project or {}).get("deliverable"),
            sources=sources,
            delegate_run=delegate_run if (agent.get("kind") or "") == "ORCHESTRATOR" else None,
            can_delegate=bool(roster),
        )
        held["tools"] = toolbox
        held["sources"] = sources
        held["roster"] = roster
        held["kind"] = agent.get("kind") or ""
        held["steps"] = steps
        held["files"] = []
        held["asked"] = False
        held.update(_upload_watch(gateway, task.swarm_id, seeds))
        role_lines = [it.text for it in task.context.recalled if it.kind in _ROLE_KINDS]
        role = "\n".join(role_lines) or _FALLBACK_ROLES.get(task.agent_id) or (
            f"You are agent '{task.agent_id}' in a Termitaria swarm. "
            "(stub system prompt: MemoryService is not connected, so role and rules are empty)"
        )
        facts = remembered_facts(task.context.recalled, task.swarm_id)
        if facts:
            role = f"{role}\n\n{facts}"
        knowledge = task.context.knowledge.strip()
        if knowledge:
            role = f"{role}\n\n{knowledge}"
        coworkers = roster if (agent.get("kind") or "") == "ORCHESTRATOR" else []
        system = _system(role, rules, catalog_skills, toolbox, coworkers, steps, materials_text(seeds))
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
        span = limit
        finished = False
        for round_index in range(limit + 6):
            if round_index >= span:
                break
            if _deadline_exceeded(task.budget):
                return _cancelled(state.get("seq", 0), usage)
            note = await _note_uploads(held)
            if note:
                window.append(("tool", note))
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
                    log.info("orch stop round=%s asked=%s", round_index + 1, bool(held.get("asked")))
                    if await _keep_going(held, window, text):
                        if round_index + 1 >= span:
                            span += 1
                        continue
                    text = _with_files(text, held)
                    finished = True
                    break
                if round_index == limit - 1:
                    if await _keep_going(held, window, text):
                        if round_index + 1 >= span:
                            span += 1
                        continue
                    text = _forced_ask(held, window)
                    finished = True
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
                if await _keep_going(held, window, text):
                    if round_index + 1 >= span:
                        span += 1
                    continue
                text = text or "The reply was cut off before it had any text."
                finished = True
                break
            used_tools = True
            notes: list[str] = []
            asked = ""
            log.info(
                "turn agent=%s round=%s/%s tools=%s",
                task.agent_id,
                round_index + 1,
                limit,
                ",".join(call.name for call in calls),
            )
            for call in calls:
                args = dict(call.arguments)
                if call.name in {"delegate", "ledger"} and notes:
                    prior = str(args.get("context") or "")
                    args["context"] = (prior + "\n\n" + "\n\n".join(notes)).strip()
                result = await toolbox.run(call.name, args)
                _remember_progress(held, call.name, result)
                held.setdefault("sources", set()).update(collect_urls(result))
                if call.name in _DATA_TOOLS:
                    used_data = True
                if call.name == "ask_user" and result.startswith("What I already have:"):
                    asked = result
                notes.append(f"{call.name} → {result}")
            window.append(("", text))
            window.append(("tool", "\n\n".join(notes)))
            if asked:
                if await _keep_going(held, window, None):
                    if round_index + 1 >= span:
                        span += 1
                    continue
                text = asked
                finished = True
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
                            held.setdefault("sources", set()).update(collect_urls(result))
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
                if await _keep_going(held, window, text):
                    if round_index + 1 >= span:
                        span += 1
                    continue
                finished = True
                break
        if not finished:
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


async def run_coworker(model, nc, task, project, gateway, sources, coworker_name, task_text, context) -> str:
    """同事在负责人这一次 task 里跑完。不另开 task，也不能再 delegate。"""
    started = time.time()
    name = (coworker_name or "").strip()
    agent = _find_coworker(project, name)
    if agent is None:
        choices = ", ".join(
            item.get("id") or "" for item in (project or {}).get("agents") or [] if item.get("id")
        ) or "none"
        return f"No coworker named {name}. Choices: {choices}"
    if agent.get("id") == task.agent_id:
        return "delegate cannot target the caller"
    cid = agent.get("id") or name
    log.info("delegate start coworker=%s", cid)
    root = Path(os.environ.get("TERMITARIA_ROOT", os.getcwd()))
    steps = (project or {}).get("steps") or []
    own = {skill for skill in (agent.get("skills") or []) if skill}
    loadable = [skill for skill in load_skills(root / "skills") if skill.name in own]
    toolbox = make_toolbox(
        agent=agent,
        project_id=task.swarm_id,
        steps=steps,
        skills=loadable,
        mcp_tools=load_mcp_tools(root / "mcp.json"),
        gateway=gateway,
        seeds=(project or {}).get("seeds") or [],
        sources=sources,
    )
    recalled = await _recall(nc, task.swarm_id, cid, task_text, task.task_id)
    knowledge = await _project_knowledge(task.swarm_id)
    role = (agent.get("role_prompt") or "").strip() or f"You are {cid}."
    parts = [role, "Finish the task yourself and return the result. You cannot delegate."]
    facts = remembered_facts(recalled, task.swarm_id)
    if facts:
        parts.append(facts)
    if knowledge and "no nodes yet" not in knowledge:
        parts.append(knowledge)
    system = "\n\n".join(parts)
    user_text = (task_text or "").strip()
    if (context or "").strip():
        user_text += "\n\nContext:\n" + context.strip()
    bag = _upload_watch(gateway, task.swarm_id, (project or {}).get("seeds"))
    text, notes = await _coworker_loop(model, task, system, toolbox, sources, cid, user_text, bag)
    handoff = coworker_handoff(text, notes)
    await publish_episode(
        nc,
        task.swarm_id,
        cid,
        f"delegate:{task.task_id}:{cid}",
        f"Delegated task:\n{task_text.strip()[:800]}\n\n{handoff[:3000]}",
        task.task_id,
    )
    log.info("delegate done coworker=%s elapsed=%.1fs", cid, time.time() - started)
    return handoff


async def _coworker_loop(model, task, system, toolbox: Toolbox, sources: set, cid: str, user_text: str, bag: dict):
    window = [("", user_text)]
    notes: list[str] = []
    text = ""
    schemas = toolbox.schemas()
    declared = _declared_tools(schemas)
    used_tools = False
    used_data = False
    limit = _MAX_TOOL_ROUNDS
    span = limit
    for round_index in range(limit + 6):
        if round_index >= span:
            break
        if _deadline_exceeded(task.budget):
            log.info("coworker %s deadline rounds=%s", cid, round_index)
            return "Stopped because the deadline was reached.", notes
        note = await _note_uploads(bag)
        if note:
            window.append(("tool", note))
        offer = schemas
        if used_tools and round_index == limit - 1:
            offer = None
        try:
            text, calls, _usage = await _complete(model, system, window, task, offer, declared)
        except _Budget:
            log.info("coworker %s deadline rounds=%s", cid, round_index)
            return "Stopped because the deadline was reached.", notes
        except Exception as exc:
            log.info("coworker %s round=%s error=%s", cid, round_index + 1, exc)
            return f"The coworker stopped: {exc}", notes
        if (
            not calls
            and not used_data
            and declared & _DATA_TOOLS
            and round_index < 3
        ):
            window.append(("", text))
            window.append(("tool", _DATA_NUDGE))
            continue
        if not calls:
            if await _keep_going(bag, window, text):
                if round_index + 1 >= span:
                    span += 1
                continue
            log.info("coworker %s done rounds=%s", cid, round_index + 1)
            return text or "Stopped after the tool results.", notes
        used_tools = True
        log.info(
            "coworker %s round=%s/%s tools=%s",
            cid,
            round_index + 1,
            limit,
            ",".join(call.name for call in calls),
        )
        for call in calls:
            result = await toolbox.run(call.name, dict(call.arguments))
            if sources is not None:
                sources.update(collect_urls(result))
            if call.name in _DATA_TOOLS:
                used_data = True
            notes.append(f"{call.name} → {result}")
        window.append(("", text))
        window.append(("tool", "\n\n".join(notes)))
    log.info("coworker %s hit max rounds=%s", cid, limit)
    return text or "Stopped after the tool results.", notes


def coworker_handoff(text: str, notes: list[str]) -> str:
    """正文只留同事真正说的话。工具、步骤和文件只认这次跑过的记录。"""
    spoken = []
    for line in (text or "").splitlines():
        if line.strip().lower().startswith("tool:"):
            continue
        spoken.append(line)
    body = "\n".join(spoken).strip() or "The coworker returned no text."
    steps = []
    files = []
    for note in notes:
        if note.startswith("advance") and "was not applied" not in note and "advance requires" not in note:
            for line in note.splitlines():
                if " is now " in line:
                    steps.append(line.strip())
        if note.startswith("write_file"):
            for line in note.splitlines():
                if "Wrote " not in line:
                    continue
                path = line.split("Wrote ", 1)[1].split(" (", 1)[0].strip()
                if path:
                    files.append(path)
    tool_text = "\n\n".join(notes) if notes else "none"
    step_text = "\n".join(steps) if steps else "none"
    file_text = "\n".join(files) if files else "none"
    return (
        f"{body}\n\nTools that ran:\n{tool_text}\n\n"
        f"Steps changed:\n{step_text}\n\n"
        f"Files written:\n{file_text}"
    )


def _find_coworker(project: dict | None, name: str) -> dict | None:
    folded = (name or "").strip().lower()
    if not folded:
        return None
    agents = (project or {}).get("agents") or []
    for agent in agents:
        if agent.get("id") == name or (agent.get("id") or "").lower() == folded:
            return agent
    for agent in agents:
        if (agent.get("display_name") or "").lower() == folded:
            return agent
    for agent in agents:
        display = (agent.get("display_name") or "").lower()
        if folded in display:
            return agent
    return None


async def _recall(nc, project_id: str, agent_id: str, query: str, trace_id: str):
    if nc is None or not agent_id:
        return []
    from termitaria.memory.v1 import memory_pb2

    req = memory_pb2.RecallRequest(
        agent_id=agent_id,
        project_id=project_id or "",
        query_text=(query or "")[:1000],
        depth=memory_pb2.RECALL_DEPTH_AUTO,
        top_k=5,
    )
    try:
        msg = await nc.request("memory.recall", envelope.pack("langgraph-worker", trace_id, req), timeout=3)
        _, resp = envelope.unpack(msg.data, memory_pb2.RecallResponse)
        return list(resp.items)
    except Exception as exc:
        log.info("recall agent=%s failed: %s", agent_id, exc)
        return []


async def _project_knowledge(project_id: str, timeout_s: float = 8) -> str:
    if not project_id:
        return ""
    from services.graph.client import GraphClient
    from .tools import _kg_read

    client = GraphClient(os.environ.get("GRAPH_GRPC_ADDR", "localhost:50051"), timeout_s=timeout_s)
    try:
        return await _kg_read(client, project_id)
    except Exception as exc:
        log.info("coworker graph project=%s failed: %s", project_id, exc)
        return ""
    finally:
        await client.close()


def _upload_watch(gateway: str, project_id: str, seeds: list | None) -> dict:
    seen: set[str] = set()
    for seed in seeds or []:
        if isinstance(seed, dict) and seed.get("id"):
            seen.add(str(seed["id"]))
    return {
        "gateway": gateway,
        "project_id": project_id,
        "seen_seeds": seen,
        "kg_left": 0,
        "kg_text": "",
        "upload_extra": 0,
    }


def _graph_note(text: str) -> str:
    return (
        "The knowledge graph changed after the upload. "
        "These are the current entities and relations:\n"
        + text
    )


async def _note_uploads(bag: dict, wait: bool = False) -> str:
    """任务进行中读到的新文件。wait 时多等一会儿，让抽取出来的关系也能进窗口。"""
    gateway = bag.get("gateway") or ""
    project_id = bag.get("project_id") or ""
    if not gateway or not project_id:
        return ""
    try:
        project = await asyncio.to_thread(load_project, gateway, project_id)
        fresh = fresh_uploads((project or {}).get("seeds") if project else None, bag.setdefault("seen_seeds", set()))
        parts: list[str] = []
        if fresh:
            log.info("upload seen project=%s files=%s", project_id, len(fresh))
            parts.append(upload_notice(fresh))
            base = (await _project_knowledge(project_id, timeout_s=3) or "").strip()
            bag["kg_text"] = base
            bag["kg_left"] = 4
            bag["polled"] = False
            bag["told_pending"] = False
            if base and "no nodes yet" not in base:
                parts.append(_graph_note(base))
            if not wait:
                return "\n\n".join(parts)
        if int(bag.get("kg_left") or 0) <= 0:
            return "\n\n".join(parts)
        if wait:
            if bag.get("polled"):
                text = (await _project_knowledge(project_id, timeout_s=3) or "").strip()
                if not text or text == bag.get("kg_text") or "no nodes yet" in text:
                    text = ""
            else:
                bag["polled"] = True
                text = await _poll_graph(project_id, str(bag.get("kg_text") or ""))
            if text:
                bag["kg_text"] = text
                bag["kg_left"] = 0
                parts.append(_graph_note(text))
            elif not parts and not bag.get("told_pending"):
                bag["told_pending"] = True
                parts.append(
                    "The uploaded file is stored. The knowledge graph has no new relations yet. "
                    "Call kg with action query before you rely on relations from that file."
                )
            return "\n\n".join(parts)
        bag["kg_left"] = int(bag["kg_left"]) - 1
        text = (await _project_knowledge(project_id, timeout_s=3) or "").strip()
        if text and text != bag.get("kg_text") and "no nodes yet" not in text:
            bag["kg_text"] = text
            bag["kg_left"] = 0
            parts.append(_graph_note(text))
        return "\n\n".join(parts)
    except Exception:
        log.info("upload check failed project=%s", project_id, exc_info=True)
        return ""


async def _poll_graph(project_id: str, previous: str) -> str:
    for attempt in range(8):
        text = (await _project_knowledge(project_id, timeout_s=3) or "").strip()
        if text and text != previous and "no nodes yet" not in text:
            return text
        if attempt < 7:
            await asyncio.sleep(3)
    return ""


async def _keep_going(bag: dict, window: list[tuple[str, str]], spoken: str | None) -> bool:
    if int(bag.get("upload_extra") or 0) >= 4:
        return False
    note = await _note_uploads(bag, wait=True)
    if not note:
        return False
    if spoken:
        window.append(("", spoken))
    window.append(("tool", note))
    bag["upload_extra"] = int(bag.get("upload_extra") or 0) + 1
    return True


async def publish_episode(nc, project_id: str, agent_id: str, session_id: str, summary: str, trace_id: str) -> None:
    """delegate 不开会话，所以同事做完就写一条 episode。聊天会话关闭时仍由 distiller 再写一条。"""
    summary = (summary or "").strip()
    if nc is None or not agent_id or not summary:
        return
    from termitaria.memory.v1 import memory_pb2

    episode = memory_pb2.Episode(
        agent_id=agent_id,
        session_id=session_id or "none",
        summary=summary[:4000],
        occurred_at_ms=envelope.now_ms(),
        project_id=project_id or "",
    )
    episode.salient_spans.append(summary[:800])
    try:
        await nc.jetstream().publish(
            "memory.write.episode",
            envelope.pack("langgraph-worker", trace_id, memory_pb2.WriteEpisodeRequest(episode=episode)),
        )
        log.info("episode queued agent=%s session=%s", agent_id, session_id)
    except Exception:
        log.exception("episode publish agent=%s", agent_id)


def remembered_facts(items, project_id: str = "", limit: int = _MAX_EPISODES) -> str:
    project_id = (project_id or "").strip()
    lines = []
    for item in items:
        if item.kind != memory_pb2.MEMORY_KIND_EPISODE:
            continue
        # 只把当前 project 的 episode 注入提示词。未标记 project 的旧记忆不注入，
        # 避免把上一个项目的假设当成当前项目的记忆。
        if project_id and item.project_id != project_id:
            continue
        text = item.text.strip()
        if not text:
            continue
        lines.append(text)
        if len(lines) >= limit:
            break
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return (
        "Your memory from this project (episodes only you can see). "
        "When the user asks what you remember or what you already proposed, quote these. "
        "Do not say that you have no memory while this list is present.\n"
        + body
    )


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
        lines = [
            "Your team (delegate with coworker set to the id). "
            "The role under each name belongs to that coworker, not to you:"
        ]
        for person in roster:
            bound = person.get("skills") or []
            extra = f" skills: {', '.join(bound)}" if bound else ""
            lines.append(f"- {person['id']}（{person['display_name']}）{extra}")
            role = " ".join((person.get("role") or "").split())
            if role:
                lines.append(f"  Role: {role}")
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
    kind = str((held.get("deliverable") or {}).get("kind") or "brief")
    noun = "paper" if kind == "paper" else "brief"
    if "write_report" in known and (
        "too short" in known
        or "missing the section" in known
        or "needs a link" in known
        or "uses a link no tool returned" in known
    ):
        reason = known.split(" → ", 1)[-1]
        reason = reason if len(reason) <= 300 else reason[:300]
        return (
            f"What I already have:\n{known}\n\n"
            f"Question:\nThe step \"{active}\" is still open because {reason} "
            f"Should I revise the {noun} to satisfy that check and store it again?"
        )
    return (
        f"What I already have:\n{known}\n\n"
        f"Question:\nThe step \"{active}\" is still open. "
        f"Which source should I open before I write the {noun}?"
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
