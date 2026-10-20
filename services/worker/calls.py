"""把各家工具调用收成同一种内部调用。

业务循环只看 name、arguments。OpenAI 的 function、
Anthropic 的 name/input、Gemini 的 function_call、以及正文里的 JSON，都从这里过。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class Call:
    name: str
    arguments: dict


def extract_tool_call(raw: Any) -> Call | None:
    """从一条供应商调用里取出名字和参数。认不出就返回 None。"""
    if hasattr(raw, "function") and getattr(raw, "function", None) is not None:
        function = raw.function
        return _make(_read(function, "name"), _read(function, "arguments"), raw)
    if hasattr(raw, "function_call") and getattr(raw, "function_call", None):
        function = raw.function_call
        args = _read(function, "args")
        if args is None:
            args = {}
        return _make(_read(function, "name"), args, raw)
    if hasattr(raw, "name") and hasattr(raw, "input"):
        return _make(_read(raw, "name"), _read(raw, "input"), raw)
    if isinstance(raw, dict):
        function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
        # tool 和 name 同时出现时，tool 是工具名，name 是参数（load_skill 就是这样）。
        if raw.get("tool"):
            name = raw.get("tool")
        else:
            name = function.get("name") or raw.get("name") or ""
        args = (
            function.get("arguments")
            if function.get("arguments") not in (None, "", {})
            else raw.get("arguments")
            if raw.get("arguments") not in (None, "", {})
            else raw.get("input")
            if raw.get("input") not in (None, "")
            else raw.get("parameters")
            if raw.get("parameters") not in (None, "")
            else raw.get("args")
        )
        if args in (None, "", {}):
            skip = {"tool", "arguments", "args", "input", "parameters", "id", "call_id", "index", "function", "type"}
            if not raw.get("tool"):
                skip.add("name")
            args = {key: value for key, value in raw.items() if key not in skip}
        return _make(str(name), args, raw)
    return None


def parse_args(raw: Any, name: str) -> dict:
    """参数是字符串就 json.loads。解不开就抛给调用方，不拿空字典去跑工具。"""
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} 的参数不是合法 JSON：{exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{name} 的参数必须是对象")
        return parsed
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    raise ValueError(f"{name} 的参数无法识别")


def _make(name: Any, args: Any, raw: Any) -> Call | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        parsed = parse_args(args, name.strip())
    except ValueError:
        parsed = {}
        if isinstance(raw, dict):
            parsed = {
                key: value
                for key, value in raw.items()
                if key not in {"tool", "name", "arguments", "args", "input", "parameters", "id", "call_id", "index", "function", "type"}
            }
    return Call(name=name.strip(), arguments=parsed)


def _read(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
