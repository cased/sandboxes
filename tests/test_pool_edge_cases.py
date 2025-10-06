"""Additional edge-case coverage for sandbox pooling utilities."""

import asyncio

import pytest

from sandboxes.base import SandboxConfig
from sandboxes.exceptions import ProviderError, SandboxQuotaError
from sandboxes.pool import ConnectionPool, PoolConfig, SandboxPool
from tests.test_pool import MockProvider


class TestSandboxPoolEdgeCases:
    """Edge-case scenarios for `SandboxPool`."""

    @pytest.mark.asyncio
    async def test_zero_max_total(self):
        pool = SandboxPool(PoolConfig(max_total=0))
        provider = MockProvider()

        with pytest.raises(SandboxQuotaError):
            await pool.acquire(provider, SandboxConfig(labels={"test": "limit"}))

    @pytest.mark.asyncio
    async def test_concurrent_acquire_release(self):
        pool = SandboxPool(PoolConfig(max_total=2))
        provider = MockProvider()
        results = []

        async def worker(task_id: int) -> None:
            sandbox = await pool.acquire(provider, SandboxConfig(labels={"task": str(task_id % 2)}))
            results.append(sandbox.id)
            await asyncio.sleep(0.01)
            await pool.release(sandbox.id)

        await asyncio.gather(*(worker(i) for i in range(10)))
        assert len(results) == 10
        # Only two sandboxes should ever be in rotation because max_total=2
        assert len(set(results)) <= 2

    @pytest.mark.asyncio
    async def test_cleanup_ignores_busy_sandboxes(self):
        pool = SandboxPool(PoolConfig(max_total=2, sandbox_ttl=10, idle_timeout=0.1))
        provider = MockProvider()

        sandbox = await pool.acquire(provider, SandboxConfig(labels={"test": "busy"}))
        await asyncio.sleep(0.05)
        cleaned = await pool.cleanup_expired()
        assert cleaned == 0  # Busy sandbox should not be cleaned

        await pool.release(sandbox.id)

    @pytest.mark.asyncio
    async def test_cleanup_removes_idle_sandboxes(self):
        pool = SandboxPool(PoolConfig(max_total=2, sandbox_ttl=10, idle_timeout=0.05))
        provider = MockProvider()
        sandbox = await pool.acquire(provider, SandboxConfig(labels={"test": "idle"}))
        await pool.release(sandbox.id)

        await asyncio.sleep(0.06)
        cleaned = await pool.cleanup_expired()
        assert cleaned == 1

    @pytest.mark.asyncio
    async def test_provider_destroy_error_does_not_crash(self):
        class FailingDestroyProvider(MockProvider):
            async def destroy_sandbox(self, sandbox_id: str) -> bool:  # type: ignore[override]
                raise ProviderError("Destroy failed")

        provider = FailingDestroyProvider()
        pool = SandboxPool(PoolConfig(max_total=1))

        sandbox = await pool.acquire(provider, SandboxConfig(labels={"test": "fail"}))
        await pool.release(sandbox.id)

        # Should swallow destroy error and continue operating
        cleaned = await pool.cleanup_expired()
        assert cleaned == 0

    @pytest.mark.asyncio
    async def test_ttl_expiration_forces_recreation(self):
        pool = SandboxPool(PoolConfig(max_total=1, sandbox_ttl=0.05, idle_timeout=1))
        provider = MockProvider()

        sandbox1 = await pool.acquire(provider, SandboxConfig(labels={"test": "ttl"}))
        sandbox_id = sandbox1.id
        await pool.release(sandbox_id)

        await asyncio.sleep(0.06)
        await pool.cleanup_expired()

        sandbox2 = await pool.acquire(provider, SandboxConfig(labels={"test": "ttl"}))
        assert sandbox2.id != sandbox_id


class TestConnectionPoolEdgeCases:
    """Edge cases for the lightweight `ConnectionPool`."""

    @pytest.mark.asyncio
    async def test_concurrent_connections(self):
        provider = MockProvider()
        pool = ConnectionPool(provider=provider, max_connections=2)

        async def worker(idx: int):
            config = SandboxConfig(labels={"slot": str(idx % 2)})
            sandbox = await pool.get_or_create(config)
            await asyncio.sleep(0.01)
            await pool.release(sandbox)

        await asyncio.gather(*(worker(i) for i in range(10)))
        metrics = pool.get_metrics()
        assert metrics["total_connections"] <= 2

    @pytest.mark.asyncio
    async def test_create_failure_propagates(self):
        class FailingProvider(MockProvider):
            async def create_sandbox(self, config: SandboxConfig):  # type: ignore[override]
                raise ProviderError("boom")

        pool = ConnectionPool(provider=FailingProvider(), max_connections=1)

        with pytest.raises(ProviderError):
            await pool.get_or_create(SandboxConfig(labels={"test": "boom"}))
