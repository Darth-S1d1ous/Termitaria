"""JetStream durable pull consumer。

- 默认一个 consumer，filter `tasks.*.*`：swarm 里新建的 agent 不用改启动命令。
  设了 WORKER_AGENTS 时仍是每 agent 一个（`tasks.*.<agent>`），便于按队列深度扩缩。
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


async def _retire_per_agent_consumers(js) -> None:
    # TASKS 是 WorkQueue，filter 不能重叠。旧的 worker-<id> 会挡住 tasks.*.*。
    try:
        infos = await js.consumers_info(_STREAM)
    except Exception:
        log.info("no existing TASKS consumers to retire")
        return
    for info in infos:
        if not info.name.startswith("worker-"):
            continue
        await js.delete_consumer(_STREAM, info.name)
        log.info("retired overlapping consumer %s", info.name)


class AgentConsumer:
    def __init__(self, nc, agent_id: str | None, runner: TaskRunner, cfg: WorkerConfig) -> None:
        self._nc = nc
        self._js = nc.jetstream()
        self._agent_id = agent_id
        self._runner = runner
        self._cfg = cfg
        self._seen: set[str] = set()  # 内存级幂等表；MVP 不持久化
        self._running: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(cfg.concurrency)

    @property
    def durable(self) -> str:
        if self._agent_id:
            return f"worker-{self._agent_id}"
        return "worker"

    @property
    def subject(self) -> str:
        if self._agent_id:
            return f"tasks.*.{self._agent_id}"
        return "tasks.*.*"

    async def run(self) -> None:
        # presence 不进 JetStream。Gateway 收到后改成 WebSocket 帧。
        # 全队列模式不在这里报在线：agent 由 SwarmSpec / 创建项目时登记。
        if self._agent_id:
            await self._nc.publish(f"presence.{self._agent_id}", b"online")
        await self._nc.subscribe("tasks.cancel.*", cb=self._on_cancel)
        try:
            await self._loop()
        finally:
            if self._agent_id:
                await self._nc.publish(f"presence.{self._agent_id}", b"offline")

    async def _loop(self) -> None:
        if self._agent_id is None:
            await _retire_per_agent_consumers(self._js)
        sub = await self._js.pull_subscribe(self.subject, durable=self.durable, stream=_STREAM)
        log.info("consumer up: durable=%s filter=%s", self.durable, self.subject)
        pending: set[asyncio.Task] = set()
        while True:
            try:
                msgs = await sub.fetch(self._cfg.concurrency, timeout=self._cfg.fetch_timeout_s)
            except (nats.errors.TimeoutError, asyncio.TimeoutError):
                # nats.py 新版抛 asyncio.TimeoutError（= 内建 TimeoutError），旧版抛 nats.errors.TimeoutError
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
        self._running[task.task_id] = asyncio.current_task()
        try:
            await self._runner.run(task, env.trace_id)
        except asyncio.CancelledError:
            # 派活超时。ack 掉，避免 JetStream 把同一单再投一次。
            log.info("task %s cancelled", task.task_id)
            await msg.ack()
            return
        except Exception:
            # result 没发出去（多半是 NATS 抖动）→ nak 让 JetStream 重投
            log.exception("task %s failed before result, nak", task.task_id)
            await msg.nak()
            return
        finally:
            self._running.pop(task.task_id, None)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

        self._seen.add(task.task_id)
        await msg.ack()
        log.info("task %s done (trace=%s)", task.task_id, env.trace_id)

    async def _on_cancel(self, msg) -> None:
        task_id = msg.subject.removeprefix("tasks.cancel.")
        running = self._running.get(task_id)
        if running is not None and not running.done():
            log.info("cancel %s", task_id)
            running.cancel()

    @staticmethod
    async def _heartbeat(msg) -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_S)
                await msg.in_progress()
        except asyncio.CancelledError:
            return
