"""DeepSeek V4 的 DSML 工具调用漏进正文时的补丁。

模型本该写 <｜DSML｜tool_calls>，服务端用 deepseek_v4 解析器收成 tool_calls。
vLLM #48931、#52645：开头标记漏掉或写坏时，完整的 invoke 会留在 content，tool_calls 为空。
这里把这种泄漏收成普通调用。只接受本次请求里声明过的工具名。
NVIDIA 或 vLLM 的解析器修好之后，删掉这个文件，并去掉 graph.py 里对 recover_dsml 的调用。
"""
from __future__ import annotations

import json
import re

from .calls import Call

# 官方接口有时把全角竖线写成两根。一根和两根都认。
_BAR = "\uff5c"
_MARK = r"\uff5c+"
_INVOKE = re.compile(
    rf"<{_MARK}DSML{_MARK}\s*invoke\s+name=\"([^\"]+)\"\s*>(.*?)</{_MARK}DSML{_MARK}\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PARAM = re.compile(
    rf"<{_MARK}DSML{_MARK}\s*parameter\s+name=\"([^\"]+)\""
    rf"(?:\s+string=\"(true|false)\")?\s*>(.*?)</{_MARK}DSML{_MARK}\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_WRAPPER = re.compile(
    rf"</?{_MARK}DSML{_MARK}\s*(?:tool_calls|toolcalls|function_calls|calls)\s*>",
    re.IGNORECASE,
)


def recover_dsml(text: str, declared: set[str]) -> tuple[str, list[Call]]:
    """从正文里捞出完整的 invoke。返回去掉这些调用之后的正文，以及调用列表。"""
    if not text or not declared or f"{_BAR}DSML{_BAR}" not in text:
        return text, []
    calls: list[Call] = []
    pieces: list[str] = []
    cursor = 0
    for match in _INVOKE.finditer(text):
        name = match.group(1).strip()
        if name not in declared:
            continue
        arguments = _parameters(match.group(2))
        calls.append(Call(name=name, arguments=arguments))
        pieces.append(text[cursor:match.start()])
        cursor = match.end()
    if not calls:
        return text, []
    pieces.append(text[cursor:])
    cleaned = _WRAPPER.sub("", "".join(pieces))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls


def _parameters(body: str) -> dict:
    arguments: dict = {}
    for match in _PARAM.finditer(body):
        key = match.group(1).strip()
        as_string = (match.group(2) or "").lower()
        raw = match.group(3).strip()
        arguments[key] = _value(raw, as_string)
    return arguments


def _value(raw: str, as_string: str):
    if as_string == "true":
        return raw
    if as_string == "false":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if raw[:1] in "[{":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw
