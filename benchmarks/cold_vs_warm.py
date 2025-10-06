#!/usr/bin/env python
"""Benchmark to isolate cold vs warm startup patterns."""

import asyncio
import os
import sys
import time
from statistics import mean, median
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandboxes import SandboxConfig
from sandboxes.providers.daytona import DaytonaProvider
from sandboxes.providers.e2b import E2BProvider
from sandboxes.providers.modal import ModalProvider


async def test_cold_startup(provider, provider_name: str, config: SandboxConfig) -> Dict:
    """Test completely cold startup - first sandbox after provider init."""
    print(f"\n🥶 Testing COLD startup for {provider_name}")

    # Measure cold start
    start = time.time()
    sandbox = await provider.create_sandbox(config)
    cold_create_time = (time.time() - start) * 1000

    print(f"   Cold create: {cold_create_time:.0f}ms")

    # Quick execution test
    start = time.time()
    await provider.execute_command(sandbox.id, "echo 'cold test'")
    cold_execute_time = (time.time() - start) * 1000

    print(f"   Cold execute: {cold_execute_time:.0f}ms")

    # Destroy
    start = time.time()
    await provider.destroy_sandbox(sandbox.id)
    cold_destroy_time = (time.time() - start) * 1000

    print(f"   Cold destroy: {cold_destroy_time:.0f}ms")

    return {
        "create": cold_create_time,
        "execute": cold_execute_time,
        "destroy": cold_destroy_time,
        "total": cold_create_time + cold_execute_time + cold_destroy_time,
    }


async def test_warm_startup(
    provider, provider_name: str, config: SandboxConfig, iterations: int = 5
) -> Dict:
    """Test warm startup - multiple sandboxes in sequence."""
    print(f"\n🔥 Testing WARM startup for {provider_name} ({iterations} iterations)")

    create_times = []
    execute_times = []
    destroy_times = []

    for i in range(iterations):
        # Create
        start = time.time()
        sandbox = await provider.create_sandbox(config)
        create_time = (time.time() - start) * 1000
        create_times.append(create_time)

        # Execute
        start = time.time()
        await provider.execute_command(sandbox.id, f"echo 'warm test {i+1}'")
        execute_time = (time.time() - start) * 1000
        execute_times.append(execute_time)

        # Destroy
        start = time.time()
        await provider.destroy_sandbox(sandbox.id)
        destroy_time = (time.time() - start) * 1000
        destroy_times.append(destroy_time)

        print(
            f"   Run {i+1}: Create={create_time:.0f}ms Execute={execute_time:.0f}ms Destroy={destroy_time:.0f}ms"
        )

        # Small delay to avoid rate limiting
        await asyncio.sleep(0.2)

    return {
        "create_times": create_times,
        "execute_times": execute_times,
        "destroy_times": destroy_times,
        "create_median": median(create_times),
        "execute_median": median(execute_times),
        "destroy_median": median(destroy_times),
        "total_median": median(
            [c + e + d for c, e, d in zip(create_times, execute_times, destroy_times)]
        ),
    }


async def test_concurrent_warm(
    provider, provider_name: str, config: SandboxConfig, concurrency: int = 3
) -> Dict:
    """Test concurrent sandbox creation to see if there's shared warm state."""
    print(f"\n⚡ Testing CONCURRENT creation for {provider_name} ({concurrency} concurrent)")

    async def create_execute_destroy(index: int):
        start_total = time.time()

        # Create
        start = time.time()
        sandbox = await provider.create_sandbox(config)
        create_time = (time.time() - start) * 1000

        # Execute
        start = time.time()
        await provider.execute_command(sandbox.id, f"echo 'concurrent test {index}'")
        execute_time = (time.time() - start) * 1000

        # Destroy
        start = time.time()
        await provider.destroy_sandbox(sandbox.id)
        destroy_time = (time.time() - start) * 1000

        total_time = (time.time() - start_total) * 1000

        return {
            "index": index,
            "create": create_time,
            "execute": execute_time,
            "destroy": destroy_time,
            "total": total_time,
        }

    # Launch concurrent tasks
    start_all = time.time()
    tasks = [create_execute_destroy(i) for i in range(concurrency)]
    results = await asyncio.gather(*tasks)
    elapsed_all = (time.time() - start_all) * 1000

    for result in results:
        print(
            f"   Concurrent {result['index']}: Create={result['create']:.0f}ms "
            f"Execute={result['execute']:.0f}ms Destroy={result['destroy']:.0f}ms "
            f"Total={result['total']:.0f}ms"
        )

    create_times = [r["create"] for r in results]
    execute_times = [r["execute"] for r in results]
    total_times = [r["total"] for r in results]

    print(f"   Wall clock time: {elapsed_all:.0f}ms")
    print(f"   Avg individual: {mean(total_times):.0f}ms")
    print(f"   Efficiency: {(sum(total_times)/elapsed_all):.1f}x")

    return {
        "create_times": create_times,
        "execute_times": execute_times,
        "create_median": median(create_times),
        "execute_median": median(execute_times),
        "wall_clock": elapsed_all,
        "efficiency": sum(total_times) / elapsed_all,
    }


async def test_provider_warmup_patterns(provider_class, provider_name: str):
    """Test complete warmup patterns for a provider."""
    print(f"\n{'='*80}")
    print(f"🔬 WARMUP ANALYSIS: {provider_name}")
    print(f"{'='*80}")

    try:
        # Initialize fresh provider
        provider = provider_class()

        # Configure for provider
        config = SandboxConfig(labels={"test": "warmup"})
        if provider_name == "Modal":
            config.image = "python:3.11-slim"
        elif provider_name == "Daytona":
            config.image = "daytonaio/ai-test:0.2.3"

        # Test 1: Cold startup
        cold_results = await test_cold_startup(provider, provider_name, config)

        # Small delay to ensure cold/warm separation
        await asyncio.sleep(2)

        # Test 2: Warm startup sequence
        warm_results = await test_warm_startup(provider, provider_name, config, iterations=5)

        # Small delay
        await asyncio.sleep(1)

        # Test 3: Concurrent warmup
        concurrent_results = await test_concurrent_warm(
            provider, provider_name, config, concurrency=3
        )

        # Analysis
        print(f"\n📊 WARMUP ANALYSIS FOR {provider_name}")
        print(f"{'='*60}")

        print("\nCold vs Warm Comparison:")
        cold_total = cold_results["total"]
        warm_total = warm_results["total_median"]
        speedup = cold_total / warm_total if warm_total > 0 else 1

        print(f"  Cold total:     {cold_total:.0f}ms")
        print(f"  Warm median:    {warm_total:.0f}ms")
        print(f"  Warmup benefit: {speedup:.2f}x faster")

        # Component analysis
        print("\nComponent Warmup Benefits:")
        cold_create = cold_results["create"]
        warm_create = warm_results["create_median"]
        create_speedup = cold_create / warm_create if warm_create > 0 else 1

        cold_execute = cold_results["execute"]
        warm_execute = warm_results["execute_median"]
        execute_speedup = cold_execute / warm_execute if warm_execute > 0 else 1

        print(f"  Create: {cold_create:.0f}ms → {warm_create:.0f}ms ({create_speedup:.2f}x)")
        print(f"  Execute: {cold_execute:.0f}ms → {warm_execute:.0f}ms ({execute_speedup:.2f}x)")

        # Variance analysis
        create_variance = max(warm_results["create_times"]) - min(warm_results["create_times"])
        execute_variance = max(warm_results["execute_times"]) - min(warm_results["execute_times"])

        print("\nWarm Performance Stability:")
        print(f"  Create variance: {create_variance:.0f}ms")
        print(f"  Execute variance: {execute_variance:.0f}ms")

        # Concurrency analysis
        print("\nConcurrency Efficiency:")
        print(f"  Sequential warm: {warm_create:.0f}ms create")
        print(f"  Concurrent avg:  {concurrent_results['create_median']:.0f}ms create")
        print(f"  Concurrency efficiency: {concurrent_results['efficiency']:.1f}x")

        return {
            "provider": provider_name,
            "cold": cold_results,
            "warm": warm_results,
            "concurrent": concurrent_results,
            "speedup": speedup,
            "create_speedup": create_speedup,
            "execute_speedup": execute_speedup,
        }

    except Exception as e:
        print(f"❌ Error testing {provider_name}: {e}")
        return None


async def main():
    """Run warmup analysis for all providers."""
    print("🔥 COLD VS WARM STARTUP ANALYSIS")
    print("=" * 80)
    print("Testing startup patterns across providers...")

    providers_to_test = [
        (ModalProvider, "Modal"),
        (E2BProvider, "E2B") if os.getenv("E2B_API_KEY") else None,
        (DaytonaProvider, "Daytona") if os.getenv("DAYTONA_API_KEY") else None,
    ]

    # Filter out None values
    providers_to_test = [p for p in providers_to_test if p is not None]

    results = []
    for provider_class, name in providers_to_test:
        result = await test_provider_warmup_patterns(provider_class, name)
        if result:
            results.append(result)

        # Delay between providers
        await asyncio.sleep(3)

    # Final comparison
    if results:
        print(f"\n{'='*80}")
        print("🏆 WARMUP COMPARISON SUMMARY")
        print(f"{'='*80}")

        print(
            f"\n{'Provider':<10} {'Cold (ms)':<12} {'Warm (ms)':<12} {'Speedup':<10} {'Best Component':<15}"
        )
        print("-" * 65)

        for r in results:
            best_component = "Create" if r["create_speedup"] > r["execute_speedup"] else "Execute"
            print(
                f"{r['provider']:<10} "
                f"{r['cold']['total']:<12.0f} "
                f"{r['warm']['total_median']:<12.0f} "
                f"{r['speedup']:<10.2f} "
                f"{best_component:<15}"
            )

        # Find best warmup provider
        best_warmup = max(results, key=lambda x: x["speedup"])
        print(
            f"\n🏆 Best warmup benefit: {best_warmup['provider']} ({best_warmup['speedup']:.2f}x faster)"
        )

        most_stable = min(
            results, key=lambda x: max(x["warm"]["create_times"]) - min(x["warm"]["create_times"])
        )
        print(f"⚖️  Most stable warm: {most_stable['provider']}")


if __name__ == "__main__":
    asyncio.run(main())
