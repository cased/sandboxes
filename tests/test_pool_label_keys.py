import pytest

from sandboxes.base import SandboxConfig
from sandboxes.pool import PoolConfig, SandboxPool
from tests.test_pool import MockProvider


@pytest.mark.asyncio
async def test_colons_do_not_collapse_distinct_label_pairs():
    provider = MockProvider()
    pool = SandboxPool(PoolConfig(auto_cleanup=False))
    try:
        first = await pool.acquire(provider, SandboxConfig(labels={"scope:project": "a"}))
        await pool.release(first.id)
        assert await pool.find_by_labels({"scope": "project:a"}) == []
        second = await pool.acquire(provider, SandboxConfig(labels={"scope": "project:a"}))
        assert second.id != first.id
        assert [sandbox.id for sandbox in await pool.find_by_labels({"scope:project": "a"})] == [
            first.id
        ]
        assert [sandbox.id for sandbox in await pool.find_by_labels({"scope": "project:a"})] == [
            second.id
        ]
    finally:
        await pool.stop()
