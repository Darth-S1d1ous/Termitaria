"""Termitaria Python 服务。gen/ 不入库，任何 services.* 导入前先把 gen/python 放进 sys.path。"""
import sys as _sys
from pathlib import Path as _Path

_GEN = _Path(__file__).resolve().parents[1] / "gen" / "python"
if _GEN.is_dir() and str(_GEN) not in _sys.path:
    _sys.path.insert(0, str(_GEN))
