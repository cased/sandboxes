#!/usr/bin/env python
"""Example of using connection pooling for better performance."""

import asyncio
import time

from sandboxes import SandboxConfig
from sandboxes.pool import PoolConfig, PoolStrategy, SandboxPool
from sandboxes.providers.modal import ModalProvider


async def main():
    """Demonstrate connection pooling."""

    print("🏊 Connection Pool Example")
    print("=" * 50)

    # Initialize provider
    try:
        provider = ModalProvider()
    except:
        print("❌ Modal not configured")
        return

    # Configure pool
    pool_config = PoolConfig(
        max_total=5,  # Maximum total sandboxes
        max_idle=3,  # Maximum idle sandboxes
        sandbox_ttl=300,  # 5 minutes TTL
        idle_timeout=60,  # 1 minute idle timeout
        strategy=PoolStrategy.EAGER,  # Pre-warm sandboxes
    )

    # Create pool
    pool = SandboxPool(provider, pool_config)

    print("\n📊 Pool Configuration:")
    print(f"   Max total: {pool_config.max_total}")
    print(f"   Max idle: {pool_config.max_idle}")
    print(f"   Strategy: {pool_config.strategy.value}")

    # Example 1: Reusing sandboxes
    print("\n1️⃣ Sandbox reuse demonstration")

    config = SandboxConfig(labels={"pool": "demo", "type": "python"}, image="python:3.11-slim")

    # First request - creates new sandbox
    start = time.time()
    sandbox1 = await pool.acquire(config)
    time1 = (time.time() - start) * 1000
    print(f"   First acquire: {time1:.0f}ms (cold start)")

    # Execute command
    result = await provider.execute_command(sandbox1.id, "echo 'First execution'")
    print(f"   Executed: {result.stdout.strip()}")

    # Return to pool
    await pool.release(sandbox1.id)
    print("   Released sandbox back to pool")

    # Second request - reuses existing sandbox
    start = time.time()
    sandbox2 = await pool.acquire(config)
    time2 = (time.time() - start) * 1000
    print(f"   Second acquire: {time2:.0f}ms (warm - {time1/time2:.1f}x faster)")

    # Should be the same sandbox
    if sandbox1.id == sandbox2.id:
        print(f"   ✅ Reused same sandbox: {sandbox1.id[:20]}...")

    await pool.release(sandbox2.id)

    # Example 2: Concurrent requests
    print("\n2️⃣ Concurrent request handling")

    async def worker(worker_id: int, pool: SandboxPool, config: SandboxConfig):
        """Worker that acquires sandbox, executes command, and releases."""
        try:
            sandbox = await pool.acquire(config)
            await provider.execute_command(sandbox.id, f"echo 'Worker {worker_id}' && sleep 0.5")
            await pool.release(sandbox.id)
            return f"Worker {worker_id}: Success"
        except Exception as e:
            return f"Worker {worker_id}: Failed - {e}"

    # Launch concurrent workers
    workers = []
    for i in range(8):  # More workers than max_total to test queuing
        workers.append(worker(i, pool, config))

    print(f"   Launching 8 workers with max pool size of {pool_config.max_total}...")
    start = time.time()
    results = await asyncio.gather(*workers)
    elapsed = time.time() - start

    successful = sum(1 for r in results if "Success" in r)
    print(f"   Completed in {elapsed:.1f}s")
    print(f"   Results: {successful}/8 successful")

    # Example 3: Pool statistics
    print("\n3️⃣ Pool statistics")

    stats = await pool.get_stats()
    print(f"   Active sandboxes: {stats['active_count']}")
    print(f"   Idle sandboxes: {stats['idle_count']}")
    print(f"   Total created: {stats['total_created']}")
    print(f"   Cache hits: {stats['hits']}")
    print(f"   Cache misses: {stats['misses']}")
    if stats["hits"] + stats["misses"] > 0:
        hit_rate = stats["hits"] / (stats["hits"] + stats["misses"]) * 100
        print(f"   Hit rate: {hit_rate:.1f}%")

    # Example 4: Different configurations
    print("\n4️⃣ Multiple configurations")

    configs = [
        SandboxConfig(labels={"env": "dev"}, image="python:3.11-slim"),
        SandboxConfig(labels={"env": "test"}, image="python:3.10-slim"),
        SandboxConfig(labels={"env": "prod"}, image="python:3.12-slim"),
    ]

    for cfg in configs:
        sandbox = await pool.acquire(cfg)
        result = await provider.execute_command(sandbox.id, "python3 --version")
        print(f"   {cfg.labels['env']}: {result.stdout.strip()}")
        await pool.release(sandbox.id)

    # Example 5: Health checks
    print("\n5️⃣ Health check demonstration")

    # Set health check
    async def health_check(sandbox_id: str) -> bool:
        """Check if sandbox is healthy."""
        result = await provider.execute_command(sandbox_id, "echo 'alive'")
        return result.success and "alive" in result.stdout

    pool.set_health_check(health_check, interval=30)

    # Trigger health check
    print("   Running health checks...")
    healthy_count = await pool.check_health()
    print(f"   Healthy sandboxes: {healthy_count}")

    # Example 6: Cleanup
    print("\n6️⃣ Pool cleanup")

    # Clean up expired sandboxes
    cleaned = await pool.cleanup()
    print(f"   Cleaned up {cleaned} expired sandboxes")

    # Destroy all sandboxes
    await pool.destroy_all()
    print("   ✅ All sandboxes destroyed")

    # Final stats
    print("\n📈 Final Statistics")
    stats = await pool.get_stats()
    print(f"   Total sandboxes created: {stats['total_created']}")
    print(f"   Total cache hits: {stats['hits']}")
    print(f"   Total cache misses: {stats['misses']}")


if __name__ == "__main__":
    asyncio.run(main())
