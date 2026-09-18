"""JetStream durable pull consumer：每 agent 一个（接口计划 · 决策点 4）。

- filter `tasks.*.<agent>`：一个 worker 进程可跨 swarm 服务同一 agent，
  HPA 按该 durable 的 pending 深度扩缩（架构 §4）；
- ack 在 TaskResult 发布成功之后 → 至少一次投递；重投由内存 _seen 挡一道，
  进程重启后的兜底去重在 actor 侧按 task_id 做（Go 侧待办）；
- 推理期间每 10s 一次 in_progress() 心跳，防止长任务被 ack_wait 判死重投。
"""
from __future__ import annotations

import asyncio
import logging

import nats.errors

from termitaria.task.v1 import task_pb2

from . import envelope
from .config import WorkerConfig
from .runner import TaskRunner

log = logging.getLogger(__name__)

_STREAM = "TASKS"
_HEARTBEAT_S = 10.0


class AgentConsumer:
    def __init__(self, nc, agent_id: str, runner: TaskRunner, cfg: WorkerConfig) -> None:
        self._js = nc.jetstream()
        self._agent_id = agent_id
        self._runner = runner
        self._cfg = cfg
        self._seen: set[str] = set()  # 内存级幂等表；MVP 不持久化
        self._sem = asyncio.Semaphore(cfg.concurrency)

    @property
    def durable(self) -> str:
        return f"worker-{self._agent_id}"

    async def run(self) -> None:
        sub = await self._js.pull_subscribe(
            f"tasks.*.{self._agent_id}", durable=self.durable, stream=_STREAM
        )
        log.info("consumer up: durable=%s filter=tasks.*.%s", self.durable, self._agent_id)
        pending: set[asyncio.Task] = set()
        while True:
            try:
                msgs = await sub.fetch(self._cfg.concurrency, timeout=self._cfg.fetch_timeout_s)
            except nats.errors.TimeoutError:
                continue
            for msg in msgs:
                await self._sem.acquire()
                t = asyncio.create_task(self._handle(msg))
                pending.add(t)
                t.add_done_callback(lambda _: self._sem.release())
                t.add_done_callback(pending.discard)

    async def _handle(self, msg) -> None:
        try:
            env, task = envelope.unpack(msg.data, task_pb2.Task)
        except Exception:
            # 解不开的坏消息永远不会变好，ack 丢弃，不污染重投队列
            log.exception("unparseable message, ack to drop")
            await msg.ack()
            return

        if task.task_id in self._seen:
            log.info("duplicate delivery task_id=%s, ack", task.task_id)
            await msg.ack()
            return

        heartbeat = asyncio.create_task(self._heartbeat(msg))
        try:
            await self._runner.run(task, env.trace_id)
        except Exception:
            # result 没发出去（多半是 NATS 抖动）→ nak 让 JetStream 重投
            log.exception("task %s failed before result, nak", task.task_id)
            await msg.nak()
            return
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

        self._seen.add(task.task_id)
        await msg.ack()
        log.info("task %s done (trace=%s)", task.task_id, env.trace_id)

    @staticmethod
    async def _heartbeat(msg) -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_S)
                await msg.in_progress()
        except asyncio.CancelledError:
            return
