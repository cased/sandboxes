#!/usr/bin/env python
"""Compare performance across all available providers."""

import asyncio
import os
import sys
import time
from statistics import mean, median
from typing import Dict, Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandboxes import SandboxConfig


async def benchmark_provider(provider_class, name: str, runs: int = 3) -> Optional[Dict]:
    """Benchmark a single provider."""
    try:
        provider = provider_class()
        print(f"\n{'='*60}")
        print(f"📦 Benchmarking {name}")
        print(f"{'='*60}")
    except Exception as e:
        print(f"\n❌ {name} not available: {e}")
        return None

    create_times = []
    execute_times = []
    destroy_times = []
    total_times = []

    for i in range(runs):
        print(f"\nRun {i+1}/{runs}:")
        total_start = time.time()

        try:
            # Create sandbox
            start = time.time()
            config = SandboxConfig(labels={"benchmark": f"{name.lower()}_run_{i}"})
            # Use standardized image for apples-to-apples comparison
            # daytonaio/ai-test:0.2.3 includes Python 3.13 + numpy + many AI/ML packages
            if name in ["Modal", "Daytona"]:
                config.image = "daytonaio/ai-test:0.2.3"

            sandbox = await provider.create_sandbox(config)
            create_time = (time.time() - start) * 1000
            create_times.append(create_time)
            print(f"  ✅ Create: {create_time:.0f}ms")

            # Execute Python command (all providers now use shell commands)
            start = time.time()
            command = "python3 -c 'import sys; print(f\"Python {sys.version.split()[0]}\")'"
            result = await provider.execute_command(sandbox.id, command)
            execute_time = (time.time() - start) * 1000
            execute_times.append(execute_time)
            success_icon = "✅" if result.success else "❌"
            print(f"  {success_icon} Execute: {execute_time:.0f}ms (success={result.success})")

            # Destroy sandbox
            start = time.time()
            await provider.destroy_sandbox(sandbox.id)
            destroy_time = (time.time() - start) * 1000
            destroy_times.append(destroy_time)
            print(f"  ✅ Destroy: {destroy_time:.0f}ms")

            total_time = (time.time() - total_start) * 1000
            total_times.append(total_time)
            print(f"  ⏱️  Total: {total_time:.0f}ms")

            # Small delay between runs
            if i < runs - 1:
                await asyncio.sleep(0.5)

        except Exception as e:
            print(f"  ❌ Error: {e}")
            continue

    if not create_times:
        return None

    return {
        "name": name,
        "create": {
            "mean": mean(create_times),
            "median": median(create_times),
            "min": min(create_times),
            "max": max(create_times),
        },
        "execute": {
            "mean": mean(execute_times) if execute_times else 0,
            "median": median(execute_times) if execute_times else 0,
            "min": min(execute_times) if execute_times else 0,
            "max": max(execute_times) if execute_times else 0,
        },
        "destroy": {
            "mean": mean(destroy_times) if destroy_times else 0,
            "median": median(destroy_times) if destroy_times else 0,
            "min": min(destroy_times) if destroy_times else 0,
            "max": max(destroy_times) if destroy_times else 0,
        },
        "total": {
            "mean": mean(total_times),
            "median": median(total_times),
            "min": min(total_times),
            "max": max(total_times),
        },
    }


async def main():
    """Run benchmarks for all available providers."""
    print("🔬 PROVIDER PERFORMANCE COMPARISON")
    print("=" * 60)
    print("Testing with 3 runs per provider...")

    results = []

    # Test Modal (we know this works)
    from sandboxes.providers.modal import ModalProvider

    modal_result = await benchmark_provider(ModalProvider, "Modal", runs=3)
    if modal_result:
        results.append(modal_result)

    # Try E2B if available
    if os.getenv("E2B_API_KEY"):
        try:
            from sandboxes.providers.e2b import E2BProvider

            e2b_result = await benchmark_provider(E2BProvider, "E2B", runs=3)
            if e2b_result:
                results.append(e2b_result)
        except Exception as e:
            print(f"\n❌ E2B error: {e}")

    # Try Daytona if available
    if os.getenv("DAYTONA_API_KEY"):
        try:
            from sandboxes.providers.daytona import DaytonaProvider

            daytona_result = await benchmark_provider(DaytonaProvider, "Daytona", runs=3)
            if daytona_result:
                results.append(daytona_result)
        except Exception as e:
            print(f"\n❌ Daytona error: {e}")

    # Display comparison table
    print("\n" + "=" * 80)
    print("📊 PERFORMANCE COMPARISON (median times in milliseconds)")
    print("=" * 80)

    if not results:
        print("No providers available for comparison")
        return

    # Header
    print(f"{'Provider':<12} {'Create':<12} {'Execute':<12} {'Destroy':<12} {'Total':<12}")
    print("-" * 60)

    # Data rows
    for r in results:
        print(
            f"{r['name']:<12} "
            f"{r['create']['median']:<12.0f} "
            f"{r['execute']['median']:<12.0f} "
            f"{r['destroy']['median']:<12.0f} "
            f"{r['total']['median']:<12.0f}"
        )

    print("\n" + "=" * 80)
    print("📈 PERFORMANCE RANKINGS")
    print("=" * 80)

    # Rankings by metric
    metrics = ["create", "execute", "destroy", "total"]
    for metric in metrics:
        sorted_results = sorted(results, key=lambda x: x[metric]["median"])
        print(f"\n{metric.upper()} (fastest to slowest):")
        for i, r in enumerate(sorted_results, 1):
            print(f"  {i}. {r['name']}: {r[metric]['median']:.0f}ms")

    # Overall summary
    print("\n" + "=" * 80)
    print("🎯 SUMMARY")
    print("=" * 80)

    fastest_total = min(results, key=lambda x: x["total"]["median"])
    print(
        f"\n🏆 Fastest Overall: {fastest_total['name']} ({fastest_total['total']['median']:.0f}ms total)"
    )

    fastest_create = min(results, key=lambda x: x["create"]["median"])
    print(
        f"⚡ Fastest Creation: {fastest_create['name']} ({fastest_create['create']['median']:.0f}ms)"
    )

    fastest_exec = min(results, key=lambda x: x["execute"]["median"])
    print(
        f"🚀 Fastest Execution: {fastest_exec['name']} ({fastest_exec['execute']['median']:.0f}ms)"
    )

    fastest_destroy = min(results, key=lambda x: x["destroy"]["median"])
    print(
        f"🧹 Fastest Cleanup: {fastest_destroy['name']} ({fastest_destroy['destroy']['median']:.0f}ms)"
    )

    # Calculate throughput
    print("\n📊 Throughput (operations/second):")
    for r in sorted(results, key=lambda x: x["total"]["median"]):
        throughput = 1000 / r["total"]["median"]
        print(f"  {r['name']}: {throughput:.2f} ops/sec")


if __name__ == "__main__":
    asyncio.run(main())
