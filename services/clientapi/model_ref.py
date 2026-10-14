"""把环境里的 NVIDIA 模型名收成 ModelRef。占位名 stub-model 由 NvidiaModel 换成默认模型。"""
from __future__ import annotations

import os

from termitaria.common.v1 import common_pb2


def model_ref() -> common_pb2.ModelRef:
    name = os.environ.get("NVIDIA_MODEL", "").strip() or "stub-model"
    return common_pb2.ModelRef(provider="nvidia", model=name)
