"""新文件入库之后，把它的主张和本项目里其他文件的主张对齐。

这是主张图常用的做法：一次判断里只标 SUPPORTS 或 CONTRADICTS。
话题接近但可以同时为真的句子不连边。模型不可用时文件和它自己的主张照常留下。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from termitaria.graph.v1 import graph_pb2

from .falkor_store import project_of
from .store import GraphStore

log = logging.getLogger(__name__)

_KIND = {
    "SUPPORTS": graph_pb2.EDGE_KIND_SUPPORTS,
    "CONTRADICTS": graph_pb2.EDGE_KIND_CONTRADICTS,
}
_SYSTEM = (
    "You link claims from different files in one project. "
    "Reply with a JSON array only. Each item is "
    '{"from":"<claim id>","to":"<claim id>","relation":"SUPPORTS"|"CONTRADICTS"}. '
    "SUPPORTS means the from claim restates the to claim or gives evidence for the same fact. "
    "CONTRADICTS means the two claims cannot both be true. "
    "A later date, an expired quote, or an unaudited label does not contradict the original figure. "
    "Skip mere topical overlap. Use only the ids provided. Return [] when nothing qualifies."
)


def document_of(node_id: str) -> str:
    parent, sep, local = node_id.rpartition("/")
    if sep and re.fullmatch(r"c\d+", local):
        return parent
    return node_id


def parse_relations(raw: str, docs: dict[str, str], new_ids: set[str]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in _json_array(raw):
        if not isinstance(item, dict):
            continue
        src = str(item.get("from") or "")
        dst = str(item.get("to") or "")
        kind = str(item.get("relation") or "").strip().upper()
        if kind not in _KIND or src not in docs or dst not in docs:
            continue
        if src == dst or docs[src] == docs[dst]:
            continue
        if src not in new_ids and dst not in new_ids:
            continue
        row = (src, dst, kind)
        if row in seen:
            continue
        seen.add(row)
        found.append(row)
        if len(found) >= 8:
            break
    return found


def prompt_for(new_claims: list[tuple[str, str]], old_claims: list[tuple[str, str]]) -> str:
    def block(title: str, rows: list[tuple[str, str]]) -> str:
        lines = [title]
        for node_id, text in rows:
            lines.append(f"- id: {node_id}")
            lines.append(f"  text: {text}")
        return "\n".join(lines)

    return block("New claims:", new_claims) + "\n\n" + block("Existing claims:", old_claims)


async def link_documents(store: GraphStore, doc_id: str, *, complete_fn=None) -> int:
    project = project_of(doc_id)
    if not project:
        return 0
    subgraph = await store.query_subgraph([f"{project}/root"], 4, [], 200)
    new_claims: list[tuple[str, str]] = []
    old_claims: list[tuple[str, str]] = []
    for node in subgraph.nodes:
        if node.kind != graph_pb2.NODE_KIND_CLAIM:
            continue
        text = _claim_text(node)
        if len(text) < 40:
            continue
        row = (node.node_id, text[:500])
        if document_of(node.node_id) == doc_id:
            new_claims.append(row)
        else:
            old_claims.append(row)
    if not new_claims or not old_claims:
        return 0
    old_claims = old_claims[:12]
    ask = complete_fn or complete
    try:
        raw = await asyncio.to_thread(ask, prompt_for(new_claims, old_claims))
    except (OSError, URLError, TimeoutError, ValueError, KeyError):
        log.exception("claim linking failed doc=%s", doc_id)
        return 0
    docs = {node_id: document_of(node_id) for node_id, _ in new_claims + old_claims}
    written = 0
    for src, dst, kind in parse_relations(raw, docs, {node_id for node_id, _ in new_claims}):
        await store.upsert_edge(
            graph_pb2.Edge(
                edge_id=f"{src}/{kind}/{dst}",
                from_node_id=src,
                to_node_id=dst,
                kind=_KIND[kind],
            )
        )
        written += 1
    if written:
        log.info("linked doc=%s edges=%d", doc_id, written)
    return written


def complete(prompt: str) -> str:
    url, key, model = _endpoint()
    if not url or not key:
        log.info("claim linking skipped: no model configured")
        return ""
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 700,
        "thinking": {"type": "disabled"},
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    req = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=20) as res:
        payload = json.loads(res.read().decode())
    message = payload["choices"][0]["message"]
    return str(message.get("content") or "")


def _endpoint() -> tuple[str, str, str]:
    provider = os.environ.get("MODEL_PROVIDER", "").strip().lower()
    if provider in {"", "deepseek"}:
        key = os.environ.get("DEEPSEEK_API_KEY", "") or _secret("deepseek-secrets", "TERMITARIA_API")
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
        return base + "/chat/completions", key, model
    if provider in {"nebius", "nebius-token-factory"}:
        key = os.environ.get("NEBIUS_API_KEY", "")
        base = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
        model = os.environ.get("MODEL_TIER_SUPER") or os.environ.get("NVIDIA_MODEL", "")
        return base + "/chat/completions", key, model
    return "", "", ""


def _secret(filename: str, key: str) -> str:
    path = Path.home() / ".secrets" / filename
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    prefix = key + "="
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip().strip('"').strip("'")
    return ""


def _claim_text(node: graph_pb2.Node) -> str:
    if node.props and "text" in node.props:
        return str(node.props["text"]).strip()
    return (node.title or "").strip()


def _json_array(raw: str) -> list:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
