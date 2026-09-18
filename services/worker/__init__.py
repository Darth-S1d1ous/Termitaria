"""Termitaria Python 智能面 · LangGraph worker（架构 §4）。

gen/ 由 buf generate 产出且不入库（.gitignore），这里把 gen/python 加进 sys.path，
让 termitaria.*_pb2 可导入。正式打包（把 gen 打进 wheel）是 slice-2 的事。
"""
import sys as _sys
from pathlib import Path as _Path

_GEN = _Path(__file__).resolve().parents[2] / "gen" / "python"
if _GEN.is_dir() and str(_GEN) not in _sys.path:
    _sys.path.insert(0, str(_GEN))
