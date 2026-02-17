"""Tests for connection pooling functionality."""

import asyncio
from datetime import datetime

import pytest

from sandboxes.base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from sandboxes.exceptions import SandboxQuotaError
from sandboxes.pool import (
    ConnectionPool,
    PoolConfig,
    PoolStrategy,
    SandboxPool,
)


class MockProvider(SandboxProvider):
    """Mock provider for testing pool functionality."""

    def __init__(self):
        super().__init__()
        self.sandboxes_created = 0
        self.sandboxes_destroyed = 0
        self.create_delay = 0.01  # Small delay to simulate creation time

    @property
    def name(self) -> str:
        return "mock"

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create mock sandbox."""
        await asyncio.sleep(self.create_delay)
        self.sandboxes_created += 1
        return Sandbox(
            id=f"mock-{self.sandboxes_created}",
            provider=self.name,
            state=SandboxState.RUNNING,
            labels=config.labels or {},
            created_at=datetime.now(),
        )

    async def get_sandbox(self, sandbox_id: str) -> Sandbox:
        """Get mock sandbox."""
        return Sandbox(
            id=sandbox_id,
            provider=self.name,
            state=SandboxState.RUNNING,
            labels={},
            created_at=datetime.now(),
        )

    async def list_sandboxes(self, labels=None):
        """List mock sandboxes."""
        return []

    async def execute_command(self, sandbox_id: str, command: str, **kwargs) -> ExecutionResult:
        """Execute mock command."""
        return ExecutionResult(
            exit_code=0,
            stdout=f"Output from {sandbox_id}: {command}",
            stderr="",
        )

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy mock sandbox."""
        self.sandboxes_destroyed += 1
        return True


class TestSandboxPool:
    """Test sandbox pool functionality."""

    @pytest.fixture
    def pool_config(self):
        """Create test pool configuration."""
        return PoolConfig(
            min_idle=1,
            max_total=5,
            max_idle=3,
            sandbox_ttl=10,  # Short TTL for testing
            idle_timeout=5,  # Short idle timeout
            acquire_timeout=2,
            strategy=PoolStrategy.LAZY,
            reuse_by_labels=True,
            auto_cleanup=False,  # Disable auto cleanup in tests
        )

    @pytest.fixture
    def pool(self, pool_config):
        """Create test pool."""
        return SandboxPool(pool_config)

    @pytest.mark.asyncio
    async def test_pool_initialization(self, pool):
        """Test pool initializes correctly."""
        assert pool.config.max_total == 5
        assert pool.config.max_idle == 3
        assert len(pool._pool) == 0
        assert len(pool._idle_sandboxes) == 0
        assert pool.get_stats()["created"] == 0

    @pytest.mark.asyncio
    async def test_acquire_sandbox_lazy_creation(self, pool):
        """Test lazy creation of sandboxes."""
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "lazy"})

        # Acquire a sandbox (should create new one)
        sandbox = await pool.acquire(provider, config)
        assert sandbox is not None
        assert sandbox.id == "mock-1"
        assert provider.sandboxes_created == 1

        # Verify sandbox is marked as busy
        assert sandbox.id in pool._busy_sandboxes
        assert sandbox.id not in pool._idle_sandboxes

    @pytest.mark.asyncio
    async def test_release_sandbox_to_idle(self, pool):
        """Test releasing sandbox back to idle pool."""
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "release"})

        # Acquire and release
        sandbox = await pool.acquire(provider, config)
        await pool.release(sandbox.id)

        # Verify sandbox is now idle
        assert sandbox.id in pool._idle_sandboxes
        assert sandbox.id not in pool._busy_sandboxes

    @pytest.mark.asyncio
    async def test_reuse_sandbox_by_labels(self, pool):
        """Test reusing sandboxes with matching labels."""
        provider = MockProvider()
        labels = {"app": "test", "env": "dev"}
        config = SandboxConfig(labels=labels)

        # Create and release a sandbox
        sandbox1 = await pool.acquire(provider, config)
        sandbox1_id = sandbox1.id
        await pool.release(sandbox1_id)

        # Acquire again with same labels
        sandbox2 = await pool.acquire(provider, config)

        # Should reuse the same sandbox
        assert sandbox2.id == sandbox1_id
        assert provider.sandboxes_created == 1  # Only created once

    @pytest.mark.asyncio
    async def test_max_total_limit(self, pool):
        """Test maximum total sandboxes limit."""
        provider = MockProvider()
        SandboxConfig(labels={"test": "max"})

        # Acquire max sandboxes
        sandboxes = []
        for i in range(pool.config.max_total):
            sandbox = await pool.acquire(
                provider, SandboxConfig(labels={"test": "max", "id": str(i)})
            )
            sandboxes.append(sandbox)

        # Should not be able to acquire more
        with pytest.raises(SandboxQuotaError):
            await pool.acquire(
                provider, SandboxConfig(labels={"test": "max", "id": "extra"}), timeout=0.1
            )

        # Release one and try again
        await pool.release(sandboxes[0].id)
        extra_sandbox = await pool.acquire(
            provider, SandboxConfig(labels={"test": "max", "id": "extra"})
        )
        assert extra_sandbox is not None

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, pool):
        """Test sandbox TTL expiration."""
        pool.config.sandbox_ttl = 0.5  # 0.5 seconds TTL
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "ttl"})

        # Create sandbox
        sandbox = await pool.acquire(provider, config)
        sandbox_id = sandbox.id
        await pool.release(sandbox_id)

        # Wait for TTL to expire
        await asyncio.sleep(0.6)

        # Cleanup should remove expired sandbox
        await pool.cleanup_expired()

        # Sandbox should be destroyed
        assert sandbox_id not in pool._pool
        assert provider.sandboxes_destroyed == 1

    @pytest.mark.asyncio
    async def test_idle_timeout(self, pool):
        """Test idle timeout cleanup."""
        pool.config.idle_timeout = 0.5  # 0.5 seconds idle timeout
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "idle"})

        # Create and release sandbox
        sandbox = await pool.acquire(provider, config)
        sandbox_id = sandbox.id
        await pool.release(sandbox_id)

        # Wait for idle timeout
        await asyncio.sleep(0.6)

        # Cleanup should remove idle sandbox
        await pool.cleanup_expired()

        # Sandbox should be destroyed
        assert sandbox_id not in pool._pool
        assert provider.sandboxes_destroyed == 1

    @pytest.mark.asyncio
    async def test_get_stats(self, pool):
        """Test pool statistics."""
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "stats"})

        # Create some sandboxes
        sandbox1 = await pool.acquire(provider, config)
        await pool.acquire(provider, SandboxConfig(labels={"test": "stats", "id": "2"}))
        await pool.release(sandbox1.id)

        stats = pool.get_stats()
        assert stats["created"] == 2
        assert stats["idle"] == 1
        assert stats["busy"] == 1
        assert stats["total"] == 2

    @pytest.mark.asyncio
    async def test_label_index(self, pool):
        """Test label indexing for fast lookup."""
        provider = MockProvider()

        # Create sandboxes with different labels
        labels1 = {"app": "web", "env": "prod"}
        labels2 = {"app": "api", "env": "prod"}
        labels3 = {"app": "web", "env": "dev"}

        sandbox1 = await pool.acquire(provider, SandboxConfig(labels=labels1))
        sandbox2 = await pool.acquire(provider, SandboxConfig(labels=labels2))
        sandbox3 = await pool.acquire(provider, SandboxConfig(labels=labels3))

        # Release all to idle
        await pool.release(sandbox1.id)
        await pool.release(sandbox2.id)
        await pool.release(sandbox3.id)

        # Find by labels
        found = await pool.find_by_labels({"app": "web"})
        assert len(found) == 2
        assert sandbox1.id in [s.id for s in found]
        assert sandbox3.id in [s.id for s in found]

        found = await pool.find_by_labels({"env": "prod"})
        assert len(found) == 2
        assert sandbox1.id in [s.id for s in found]
        assert sandbox2.id in [s.id for s in found]

    @pytest.mark.asyncio
    async def test_concurrent_acquire(self, pool):
        """Test concurrent acquire operations."""
        provider = MockProvider()
        SandboxConfig(labels={"test": "concurrent"})

        # Acquire multiple sandboxes concurrently
        tasks = []
        for i in range(3):
            task = pool.acquire(
                provider, SandboxConfig(labels={"test": "concurrent", "id": str(i)})
            )
            tasks.append(task)

        sandboxes = await asyncio.gather(*tasks)

        # All should be different sandboxes
        ids = [s.id for s in sandboxes]
        assert len(set(ids)) == 3
        assert provider.sandboxes_created == 3

    @pytest.mark.asyncio
    async def test_health_check(self, pool):
        """Test sandbox health checking."""
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "health"})

        # Create sandbox
        sandbox = await pool.acquire(provider, config)
        sandbox_id = sandbox.id

        # Mark as unhealthy
        pool._pool[sandbox_id].metadata["healthy"] = False

        # Health check should detect unhealthy sandbox
        unhealthy = await pool.check_health()
        assert len(unhealthy) == 1
        assert unhealthy[0] == sandbox_id

    @pytest.mark.asyncio
    async def test_eager_strategy_prewarms_idle_sandboxes(self):
        """Eager strategy should keep the configured minimum number of idle sandboxes."""
        pool = SandboxPool(
            PoolConfig(
                min_idle=2,
                max_total=5,
                max_idle=5,
                strategy=PoolStrategy.EAGER,
                auto_cleanup=False,
            )
        )
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "eager"})

        sandbox = await pool.acquire(provider, config)
        assert sandbox is not None

        stats = pool.get_stats()
        assert stats["total"] >= 2
        assert stats["idle"] >= 1
        assert stats["busy"] >= 1

    @pytest.mark.asyncio
    async def test_start_with_template_prewarms_idle(self):
        """start(provider, config) should pre-create idle sandboxes for eager pools."""
        pool = SandboxPool(
            PoolConfig(
                min_idle=2,
                max_total=5,
                max_idle=5,
                strategy=PoolStrategy.EAGER,
                auto_cleanup=False,
            )
        )
        provider = MockProvider()
        config = SandboxConfig(labels={"test": "start-eager"})

        await pool.start(provider, config)
        stats = pool.get_stats()

        assert stats["idle"] == 2
        assert stats["busy"] == 0
        assert stats["total"] == 2


class TestConnectionPool:
    """Test the ConnectionPool class specifically."""

    @pytest.fixture
    def provider(self):
        """Create mock provider."""
        return MockProvider()

    @pytest.fixture
    def connection_pool(self, provider):
        """Create connection pool."""
        return ConnectionPool(
            provider=provider,
            max_connections=3,
            max_idle_time=5,
            ttl=10,
        )

    @pytest.mark.asyncio
    async def test_connection_pool_get_or_create(self, connection_pool):
        """Test getting or creating connections."""
        config = SandboxConfig(labels={"test": "connection"})

        # First call should create
        conn1 = await connection_pool.get_or_create(config)
        assert conn1 is not None
        assert connection_pool.provider.sandboxes_created == 1

        # Second call with same config should reuse
        conn2 = await connection_pool.get_or_create(config)
        assert conn2.id == conn1.id
        assert connection_pool.provider.sandboxes_created == 1  # No new creation

    @pytest.mark.asyncio
    async def test_connection_pool_release(self, connection_pool):
        """Test releasing connections back to pool."""
        config = SandboxConfig(labels={"test": "release"})

        # Get and release
        conn = await connection_pool.get_or_create(config)
        conn_id = conn.id

        success = await connection_pool.release(conn)
        assert success

        # Connection should be available for reuse
        conn2 = await connection_pool.get_or_create(config)
        assert conn2.id == conn_id

    @pytest.mark.asyncio
    async def test_connection_pool_max_limit(self, connection_pool):
        """Test maximum connections limit."""
        configs = [SandboxConfig(labels={"test": "limit", "id": str(i)}) for i in range(4)]

        # Get max connections
        conns = []
        for i in range(3):
            conn = await connection_pool.get_or_create(configs[i])
            conns.append(conn)

        # Should not be able to get more
        with pytest.raises(SandboxQuotaError):
            await connection_pool.get_or_create(configs[3])

        # Release one and try again
        await connection_pool.release(conns[0])
        conn4 = await connection_pool.get_or_create(configs[3])
        assert conn4 is not None

    @pytest.mark.asyncio
    async def test_connection_pool_ttl(self, connection_pool):
        """Test connection TTL expiration."""
        connection_pool.ttl = 0.5  # 0.5 seconds TTL
        config = SandboxConfig(labels={"test": "ttl"})

        # Create connection
        conn = await connection_pool.get_or_create(config)
        await connection_pool.release(conn)

        # Wait for TTL
        await asyncio.sleep(0.6)

        # Cleanup expired
        await connection_pool.cleanup_expired()

        # Should have been destroyed
        assert connection_pool.provider.sandboxes_destroyed == 1

    @pytest.mark.asyncio
    async def test_connection_pool_idle_cleanup(self, connection_pool):
        """Test idle connection cleanup."""
        connection_pool.max_idle_time = 0.5  # 0.5 seconds idle
        config = SandboxConfig(labels={"test": "idle"})

        # Create and release connection
        conn = await connection_pool.get_or_create(config)
        await connection_pool.release(conn)

        # Wait for idle timeout
        await asyncio.sleep(0.6)

        # Cleanup idle
        await connection_pool.cleanup_idle()

        # Should have been destroyed
        assert connection_pool.provider.sandboxes_destroyed == 1

    @pytest.mark.asyncio
    async def test_connection_pool_metrics(self, connection_pool):
        """Test connection pool metrics."""
        config = SandboxConfig(labels={"test": "metrics"})

        # Create some connections
        conn1 = await connection_pool.get_or_create(config)
        await connection_pool.get_or_create(SandboxConfig(labels={"test": "metrics", "id": "2"}))
        await connection_pool.release(conn1)

        metrics = connection_pool.get_metrics()
        assert metrics["total_created"] == 2
        assert metrics["active_connections"] == 1
        assert metrics["idle_connections"] == 1
        assert metrics["total_connections"] == 2
