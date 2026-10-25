"""FalkorDB 驱动。一张图只建一次索引，连接池对齐 Graphiti 的并发。"""
from __future__ import annotations

import asyncio
import copy

from graphiti_core.driver.falkordb_driver import FalkorDriver

# 每一处 semaphore_gather 的上限。边去重是 4 路 search，每路再发全文和向量，大约 8 条查询。
MAX_COROUTINES = 4
# 池大小对齐上面的在途查询。满了就在进程里等，不再向 6379 加开连接。
POOL_CONNECTIONS = 8


def _silence_init(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    task.exception()


class OnceFalkorDriver(FalkorDriver):
    """构造时不建索引。clone 浅拷贝，避免再次进入 __init__。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        task = self._init_task
        self._init_task = None
        if task is not None and not task.done():
            task.cancel()
            task.add_done_callback(_silence_init)
        self._schema_lock = asyncio.Lock()
        self._ready_databases: set[str] = set()

    def clone(self, database: str) -> OnceFalkorDriver:
        if database == self._database:
            return self
        cloned = copy.copy(self)
        cloned._database = database
        cloned._init_task = None
        return cloned

    async def ensure_database_initialized(self) -> None:
        name = self._database
        async with self._schema_lock:
            if name in self._ready_databases:
                return
            await self.build_indices_and_constraints()
            self._ready_databases.add(name)


def open_driver(host: str, port: int) -> OnceFalkorDriver:
    from falkordb.asyncio import FalkorDB
    from redis.asyncio import BlockingConnectionPool

    pool = BlockingConnectionPool(
        host=host,
        port=port,
        max_connections=POOL_CONNECTIONS,
        timeout=60,
        decode_responses=True,
        protocol=2,
    )
    client = FalkorDB(connection_pool=pool)
    return OnceFalkorDriver(falkor_db=client)
