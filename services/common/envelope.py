"""Envelope 收发助手：internal/bus/envelope.go 的 Python 镜像（contracts §1.1）。

- event_id：ULID，幂等/去重键（JetStream 侧由 Go 发布者用作 Nats-Msg-Id）；
- trace_id：贯穿 用户请求 → session → task → memory；worker 收到什么就透传什么；
- schema：payload 的全限定 proto 类型名，解包时强校验，不匹配直接抛错。
"""
from __future__ import annotations

import time

import ulid
from google.protobuf.message import Message as PbMessage

from termitaria.common.v1 import common_pb2


class SchemaMismatchError(Exception):
    """Envelope.schema 与期望的 payload 类型不一致。"""


def now_ms() -> int:
    return int(time.time() * 1000)


def pack(producer: str, trace_id: str, payload: PbMessage) -> bytes:
    env = common_pb2.Envelope(
        event_id=str(ulid.new()),
        trace_id=trace_id or str(ulid.new()),
        occurred_at_ms=now_ms(),
        producer=producer,
        schema=payload.DESCRIPTOR.full_name,
        payload=payload.SerializeToString(),
    )
    return env.SerializeToString()


def unpack(data: bytes, payload_type: type[PbMessage]) -> tuple[common_pb2.Envelope, PbMessage]:
    env = common_pb2.Envelope()
    env.ParseFromString(data)
    want = payload_type.DESCRIPTOR.full_name
    if env.schema != want:
        raise SchemaMismatchError(f"schema mismatch: envelope={env.schema!r} target={want!r}")
    payload = payload_type()
    payload.ParseFromString(env.payload)
    return env, payload
