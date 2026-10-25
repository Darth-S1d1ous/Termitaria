"""clone 不再重建驱动，同一张图的索引只记一次。"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("falkordb")

from services.graph.falkor import OnceFalkorDriver


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_clone_shares_the_schema_guard_and_keeps_the_root_database():
    async def run() -> None:
        client = _Client()
        driver = OnceFalkorDriver(falkor_db=client, database="default_db")
        cloned = driver.clone("p_desk")
        assert cloned is not driver
        assert cloned._database == "p_desk"
        assert driver._database == "default_db"
        assert cloned._ready_databases is driver._ready_databases
        assert cloned._schema_lock is driver._schema_lock
        assert cloned.clone("p_desk") is cloned
        await driver.close()
        assert client.closed

    asyncio.run(run())
