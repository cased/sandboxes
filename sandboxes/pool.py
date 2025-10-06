"""Connection pooling and lifecycle management for sandboxes."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .base import Sandbox, SandboxConfig
from .exceptions import SandboxQuotaError

logger = logging.getLogger(__name__)


class PoolStrategy(Enum):
    """Pooling strategies for sandbox management."""

    EAGER = "eager"  # Pre-create sandboxes
    LAZY = "lazy"  # Create on-demand
    HYBRID = "hybrid"  # Mix of eager and lazy


@dataclass
class PoolConfig:
    """Configuration for sandbox pool."""

    # Pool size limits
    min_idle: int = 0  # Minimum idle sandboxes to maintain
    max_total: int = 10  # Maximum total sandboxes
    max_idle: int = 5  # Maximum idle sandboxes

    # Timeouts and TTL
    sandbox_ttl: int = 3600  # Sandbox time-to-live in seconds
    idle_timeout: int = 600  # Time before idle sandbox is destroyed
    acquire_timeout: int = 30  # Timeout for acquiring a sandbox

    # Behavior
    strategy: PoolStrategy = PoolStrategy.LAZY
    reuse_by_labels: bool = True  # Enable label-based reuse
    auto_cleanup: bool = True  # Enable automatic cleanup
    cleanup_interval: int = 60  # Cleanup check interval in seconds

    # Lifecycle hooks
    on_create: Callable | None = None
    on_destroy: Callable | None = None
    on_reuse: Callable | None = None


@dataclass
class SandboxPoolEntry:
    """Entry in the sandbox pool with metadata."""

    sandbox: Sandbox
    provider: Any  # The actual provider instance
    config: SandboxConfig
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    is_idle: bool = True
    labels: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class SandboxPool:
    """
    Advanced connection pool for sandbox management.

    Features:
    - Connection pooling with configurable strategies
    - Smart reuse based on labels
    - Automatic lifecycle management
    - TTL and idle timeout handling
    - Resource limits and quotas
    - Health monitoring
    """

    def __init__(self, pool_config: PoolConfig | None = None):
        """Initialize sandbox pool."""
        self.config = pool_config or PoolConfig()

        # Pool storage
        self._pool: dict[str, SandboxPoolEntry] = {}
        self._idle_sandboxes: set[str] = set()
        self._busy_sandboxes: set[str] = set()

        # Label index for fast lookup
        self._label_index: dict[str, set[str]] = {}

        # Locks for thread-safe operations
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)

        # Cleanup task
        self._cleanup_task: asyncio.Task | None = None

        # Statistics
        self._stats = {
            "created": 0,
            "destroyed": 0,
            "reused": 0,
            "timeouts": 0,
            "errors": 0,
        }

    async def start(self):
        """Start the pool and background tasks."""
        if self.config.auto_cleanup:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        # Pre-create sandboxes if using eager strategy
        if self.config.strategy == PoolStrategy.EAGER:
            await self._ensure_min_idle()

    async def stop(self):
        """Stop the pool and clean up resources."""
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        # Destroy all sandboxes
        await self.clear()

    async def acquire(
        self, provider: Any, config: SandboxConfig, timeout: int | None = None
    ) -> Sandbox:
        """
        Acquire a sandbox from the pool or create a new one.

        Args:
            provider: Sandbox provider instance
            config: Configuration for the sandbox
            timeout: Acquisition timeout in seconds

        Returns:
            Sandbox instance

        Raises:
            SandboxQuotaError: If pool limits are exceeded
            SandboxError: If acquisition fails
        """
        timeout = timeout or self.config.acquire_timeout

        if self.config.max_total <= 0:
            raise SandboxQuotaError("Pool limit reached: 0")

        eviction_entry: SandboxPoolEntry | None = None

        try:
            async with asyncio.timeout(timeout):
                while True:
                    async with self._lock:
                        # Try to find a reusable sandbox
                        if self.config.reuse_by_labels and config.labels:
                            sandbox = await self._find_reusable_sandbox(config.labels)
                            if sandbox:
                                await self._mark_busy(sandbox.id)
                                self._stats["reused"] += 1

                                if self.config.on_reuse:
                                    await self._call_hook(self.config.on_reuse, sandbox)

                                return sandbox

                        # Pool capacity check
                        if len(self._pool) >= self.config.max_total:
                            eviction_entry = self._prepare_idle_eviction_locked()
                            if not eviction_entry:
                                await self._condition.wait()
                                continue
                        else:
                            eviction_entry = None
                            sandbox = await self._create_sandbox(provider, config)
                            await self._mark_busy(sandbox.id)
                            return sandbox

                    if eviction_entry:
                        await self._finalize_eviction(eviction_entry)
                        eviction_entry = None
                        continue

        except asyncio.TimeoutError as e:
            self._stats["timeouts"] += 1
            raise SandboxQuotaError(f"Pool limit reached: {self.config.max_total}") from e
        except SandboxQuotaError:
            self._stats["errors"] += 1
            raise
        except Exception:
            self._stats["errors"] += 1
            raise

    async def release(self, sandbox_id: str):
        """
        Release a sandbox back to the pool.

        Args:
            sandbox_id: ID of the sandbox to release
        """
        evictions: list[SandboxPoolEntry] = []

        async with self._lock:
            if sandbox_id in self._pool:
                entry = self._pool[sandbox_id]
                entry.is_idle = True
                entry.last_accessed = datetime.now()

                self._busy_sandboxes.discard(sandbox_id)
                self._idle_sandboxes.add(sandbox_id)

                while len(self._idle_sandboxes) > self.config.max_idle:
                    eviction_entry = self._prepare_idle_eviction_locked()
                    if not eviction_entry:
                        break
                    evictions.append(eviction_entry)

                self._condition.notify_all()

        for entry in evictions:
            await self._finalize_eviction(entry)

    async def destroy(self, sandbox_id: str):
        """
        Destroy a sandbox and remove from pool.

        Args:
            sandbox_id: ID of the sandbox to destroy
        """
        entry = await self._pop_entry_for_destroy(sandbox_id)
        if not entry:
            return

        try:
            if self.config.on_destroy:
                await self._call_hook(self.config.on_destroy, entry.sandbox)

            await entry.provider.destroy_sandbox(sandbox_id)
            self._stats["destroyed"] += 1
        except Exception as e:
            logger.error(f"Failed to destroy sandbox {sandbox_id}: {e}")

    async def clear(self):
        """Destroy all sandboxes in the pool."""
        sandbox_ids = list(self._pool.keys())
        for sandbox_id in sandbox_ids:
            await self.destroy(sandbox_id)

    async def _create_sandbox(self, provider: Any, config: SandboxConfig) -> Sandbox:
        """Create a new sandbox and add to pool."""
        # Create sandbox
        sandbox = await provider.create_sandbox(config)

        # Create pool entry
        entry = SandboxPoolEntry(
            sandbox=sandbox,
            provider=provider,
            config=config,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            labels=config.labels or {},
        )

        # Add to pool
        self._pool[sandbox.id] = entry

        # Update label index
        for key, value in entry.labels.items():
            label_key = f"{key}:{value}"
            if label_key not in self._label_index:
                self._label_index[label_key] = set()
            self._label_index[label_key].add(sandbox.id)

        self._stats["created"] += 1

        # Call create hook
        if self.config.on_create:
            await self._call_hook(self.config.on_create, sandbox)

        return sandbox

    async def _find_reusable_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find an idle sandbox with matching labels."""
        # Build label keys
        label_keys = [f"{k}:{v}" for k, v in labels.items()]

        # Find sandboxes with all matching labels
        matching_ids = None
        for label_key in label_keys:
            sandbox_ids = self._label_index.get(label_key, set())
            if matching_ids is None:
                matching_ids = sandbox_ids.copy()
            else:
                matching_ids &= sandbox_ids

        if not matching_ids:
            return None

        # Find an idle sandbox from matching ones
        for sandbox_id in matching_ids:
            if sandbox_id in self._idle_sandboxes:
                entry = self._pool[sandbox_id]
                entry.access_count += 1
                return entry.sandbox

        return None

    async def find_by_labels(self, labels: dict[str, str]) -> list[Sandbox]:
        """Return sandboxes that match the provided labels."""
        if not labels:
            return []

        async with self._lock:
            label_keys = [f"{k}:{v}" for k, v in labels.items()]
            matching_ids: set[str] | None = None

            for label_key in label_keys:
                sandbox_ids = self._label_index.get(label_key, set())
                if matching_ids is None:
                    matching_ids = sandbox_ids.copy()
                else:
                    matching_ids &= sandbox_ids

            if not matching_ids:
                return []

            return [
                self._pool[sandbox_id].sandbox
                for sandbox_id in matching_ids
                if sandbox_id in self._pool
            ]

    async def _mark_busy(self, sandbox_id: str):
        """Mark a sandbox as busy."""
        if sandbox_id in self._pool:
            entry = self._pool[sandbox_id]
            entry.is_idle = False
            entry.last_accessed = datetime.now()

            self._idle_sandboxes.discard(sandbox_id)
            self._busy_sandboxes.add(sandbox_id)

    async def _evict_idle_sandbox(self) -> bool:
        """Evict the least recently used idle sandbox."""
        async with self._lock:
            entry = self._prepare_idle_eviction_locked()

        if not entry:
            return False

        await self._finalize_eviction(entry)
        return True

    async def _ensure_min_idle(self):
        """Ensure minimum idle sandboxes (for eager strategy)."""
        # This would need provider and config information
        # Implement based on specific requirements
        pass

    async def _remove_from_pool(self, sandbox_id: str):
        """Remove sandbox from pool and indexes."""
        async with self._lock:
            self._remove_from_pool_locked(sandbox_id)

    def _prepare_idle_eviction_locked(self) -> SandboxPoolEntry | None:
        """Choose the least-recently-used idle sandbox and remove it from tracking."""
        if not self._idle_sandboxes:
            return None

        lru_id = None
        lru_time = datetime.now()

        for sandbox_id in self._idle_sandboxes:
            entry = self._pool[sandbox_id]
            if entry.last_accessed < lru_time:
                lru_time = entry.last_accessed
                lru_id = sandbox_id

        if not lru_id:
            return None

        return self._remove_from_pool_locked(lru_id)

    def _remove_from_pool_locked(self, sandbox_id: str) -> SandboxPoolEntry | None:
        if sandbox_id not in self._pool:
            return None

        entry = self._pool.pop(sandbox_id)

        for key, value in entry.labels.items():
            label_key = f"{key}:{value}"
            if label_key in self._label_index:
                self._label_index[label_key].discard(sandbox_id)
                if not self._label_index[label_key]:
                    del self._label_index[label_key]

        self._idle_sandboxes.discard(sandbox_id)
        self._busy_sandboxes.discard(sandbox_id)

        self._condition.notify_all()

        return entry

    async def _pop_entry_for_destroy(self, sandbox_id: str) -> SandboxPoolEntry | None:
        async with self._lock:
            return self._remove_from_pool_locked(sandbox_id)

    async def _finalize_eviction(self, entry: SandboxPoolEntry) -> None:
        try:
            await entry.provider.destroy_sandbox(entry.sandbox.id)
            self._stats["destroyed"] += 1
        except Exception as e:
            logger.error(f"Failed to evict sandbox {entry.sandbox.id}: {e}")

    async def _cleanup_loop(self):
        """Background task to clean up expired sandboxes."""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    async def _cleanup_expired(self):
        """Clean up expired and idle sandboxes."""
        now = datetime.now()
        expired = []

        async with self._lock:
            for sandbox_id, entry in self._pool.items():
                # Check TTL
                age = (now - entry.created_at).total_seconds()
                if age > self.config.sandbox_ttl:
                    expired.append(sandbox_id)
                    continue

                # Check idle timeout
                if entry.is_idle:
                    idle_time = (now - entry.last_accessed).total_seconds()
                    if idle_time > self.config.idle_timeout:
                        expired.append(sandbox_id)

        # Destroy expired sandboxes
        for sandbox_id in expired:
            logger.info(f"Cleaning up expired sandbox {sandbox_id}")
            await self.destroy(sandbox_id)

    async def _call_hook(self, hook: Callable, *args, **kwargs):
        """Call a lifecycle hook safely."""
        try:
            if asyncio.iscoroutinefunction(hook):
                await hook(*args, **kwargs)
            else:
                hook(*args, **kwargs)
        except Exception as e:
            logger.error(f"Hook error: {e}")

    async def cleanup_expired(self) -> int:
        """Public helper to clean up expired or idle sandboxes."""
        destroyed_before = self._stats["destroyed"]
        await self._cleanup_expired()
        return self._stats["destroyed"] - destroyed_before

    def get_stats(self) -> dict[str, Any]:
        """Get pool statistics."""
        return {
            **self._stats,
            "total": len(self._pool),
            "idle": len(self._idle_sandboxes),
            "busy": len(self._busy_sandboxes),
        }

    async def check_health(self) -> list[str]:
        """Return IDs of sandboxes flagged as unhealthy."""
        unhealthy: list[str] = []
        async with self._lock:
            for sandbox_id, entry in self._pool.items():
                if entry.metadata.get("healthy") is False:
                    unhealthy.append(sandbox_id)
        return unhealthy

    async def health_check(self) -> dict[str, Any]:
        """Check pool health."""
        stats = self.get_stats()

        return {
            "healthy": stats["total"] < self.config.max_total,
            "stats": stats,
            "config": {
                "max_total": self.config.max_total,
                "max_idle": self.config.max_idle,
                "min_idle": self.config.min_idle,
            },
        }


class ConnectionPool:
    """Simplified connection pool for provider-specific sandboxes."""

    def __init__(
        self,
        provider,
        max_connections: int = 10,
        max_idle_time: int = 600,
        ttl: int = 3600,
    ):
        """Initialize connection pool."""
        self.provider = provider
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time
        self.ttl = ttl

        self._connections: dict[str, Sandbox] = {}
        self._idle_connections: set[str] = set()
        self._connection_metadata: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, config: SandboxConfig) -> Sandbox:
        """Get or create a connection."""
        to_destroy: Sandbox | None = None

        while True:
            async with self._lock:
                # Exact match reuse (even if currently active)
                for conn_id, metadata in self._connection_metadata.items():
                    if metadata.get("labels") == config.labels:
                        self._idle_connections.discard(conn_id)
                        metadata["last_used"] = time.time()
                        return self._connections[conn_id]

                # Create new connection if capacity available
                if len(self._connections) < self.max_connections:
                    sandbox = await self.provider.create_sandbox(config)
                    self._connections[sandbox.id] = sandbox
                    self._connection_metadata[sandbox.id] = {
                        "created_at": time.time(),
                        "last_used": time.time(),
                        "labels": config.labels,
                    }
                    return sandbox

                # Otherwise try to evict an idle connection
                evict_id = self._select_idle_connection_locked()
                if not evict_id:
                    raise SandboxQuotaError(
                        f"Connection pool limit reached: {self.max_connections}"
                    )

                to_destroy = self._connections.pop(evict_id, None)
                self._connection_metadata.pop(evict_id, None)
                self._idle_connections.discard(evict_id)

            if to_destroy:
                try:
                    await self.provider.destroy_sandbox(to_destroy.id)
                except Exception as e:
                    logger.error(f"Failed to destroy connection {to_destroy.id}: {e}")
                finally:
                    to_destroy = None
                # Loop to try allocation again after eviction
                continue

    async def release(self, connection: Sandbox) -> bool:
        """Release connection back to pool."""
        async with self._lock:
            if connection.id in self._connections:
                self._idle_connections.add(connection.id)
                self._connection_metadata[connection.id]["last_used"] = time.time()
                return True
            return False

    async def cleanup_expired(self):
        """Clean up expired connections."""
        async with self._lock:
            current_time = time.time()
            to_remove = []

            for conn_id, metadata in self._connection_metadata.items():
                age = current_time - metadata["created_at"]
                if age > self.ttl:
                    to_remove.append(conn_id)

            for conn_id in to_remove:
                await self.provider.destroy_sandbox(conn_id)
                del self._connections[conn_id]
                del self._connection_metadata[conn_id]
                self._idle_connections.discard(conn_id)

    async def cleanup_idle(self):
        """Clean up idle connections."""
        async with self._lock:
            current_time = time.time()
            to_remove = []

            for conn_id in self._idle_connections:
                metadata = self._connection_metadata.get(conn_id, {})
                idle_time = current_time - metadata.get("last_used", 0)
                if idle_time > self.max_idle_time:
                    to_remove.append(conn_id)

            for conn_id in to_remove:
                await self.provider.destroy_sandbox(conn_id)
                del self._connections[conn_id]
                del self._connection_metadata[conn_id]
                self._idle_connections.remove(conn_id)

    def get_metrics(self) -> dict[str, Any]:
        """Get pool metrics."""
        return {
            "total_created": (
                self.provider.sandboxes_created
                if hasattr(self.provider, "sandboxes_created")
                else len(self._connections)
            ),
            "total_connections": len(self._connections),
            "idle_connections": len(self._idle_connections),
            "active_connections": len(self._connections) - len(self._idle_connections),
        }

    def _select_idle_connection_locked(self) -> str | None:
        if not self._idle_connections:
            return None

        oldest_id = None
        oldest_time = time.time()

        for conn_id in self._idle_connections:
            metadata = self._connection_metadata.get(conn_id, {})
            last_used = metadata.get("last_used", 0)
            if oldest_id is None or last_used < oldest_time:
                oldest_id = conn_id
                oldest_time = last_used

        return oldest_id
