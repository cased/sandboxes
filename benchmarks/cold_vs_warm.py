#!/usr/bin/env python
"""Benchmark to isolate cold vs warm startup patterns."""

import asyncio
import os
import sys
import time
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmarks.provider_matrix import (
    benchmark_image_for_provider,
    discover_benchmark_providers,
)
from sandboxes import SandboxConfig


async def test_cold_startup(provider, provider_name: str, config: SandboxConfig) -> dict:
    """Test completely cold startup - first sandbox after provider init."""
    print(f"\n🥶 Testing COLD startup for {provider_name}")

    sandbox_id: str | None = None
    cold_create_time = 0.0
    cold_execute_time = 0.0
    cold_destroy_time = 0.0

    try:
        # Measure cold start
        start = time.time()
        sandbox = await provider.create_sandbox(config)
        sandbox_id = sandbox.id
        cold_create_time = (time.time() - start) * 1000

        print(f"   Cold create: {cold_create_time:.0f}ms")

        # Quick execution test
        start = time.time()
        await provider.execute_command(sandbox_id, "echo 'cold test'")
        cold_execute_time = (time.time() - start) * 1000

        print(f"   Cold execute: {cold_execute_time:.0f}ms")
    finally:
        if sandbox_id:
            start = time.time()
            await provider.destroy_sandbox(sandbox_id)
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
) -> dict:
    """Test warm startup - multiple sandboxes in sequence."""
    print(f"\n🔥 Testing WARM startup for {provider_name} ({iterations} iterations)")

    create_times = []
    execute_times = []
    destroy_times = []
    total_times = []

    for i in range(iterations):
        run_start = time.time()
        sandbox_id: str | None = None
        create_time = 0.0
        execute_time = 0.0
        destroy_time = 0.0
        run_success = False

        try:
            # Create
            start = time.time()
            sandbox = await provider.create_sandbox(config)
            sandbox_id = sandbox.id
            create_time = (time.time() - start) * 1000

            # Execute
            start = time.time()
            await provider.execute_command(sandbox_id, f"echo 'warm test {i+1}'")
            execute_time = (time.time() - start) * 1000
            run_success = True
        except Exception as e:
            print(f"   Run {i+1}: ❌ Failed - {str(e)[:80]}")
        finally:
            if sandbox_id:
                start = time.time()
                try:
                    await provider.destroy_sandbox(sandbox_id)
                    destroy_time = (time.time() - start) * 1000
                except Exception as cleanup_error:
                    run_success = False
                    print(f"   Run {i+1}: ⚠️  Cleanup failed - {str(cleanup_error)[:80]}")

        if run_success:
            create_times.append(create_time)
            execute_times.append(execute_time)
            destroy_times.append(destroy_time)
            total_time = (time.time() - run_start) * 1000
            total_times.append(total_time)
            print(
                f"   Run {i+1}: Create={create_time:.0f}ms Execute={execute_time:.0f}ms Destroy={destroy_time:.0f}ms"
            )

        # Small delay to avoid rate limiting
        await asyncio.sleep(0.2)

    return {
        "create_times": create_times,
        "execute_times": execute_times,
        "destroy_times": destroy_times,
        "create_median": median(create_times) if create_times else 0,
        "execute_median": median(execute_times) if execute_times else 0,
        "destroy_median": median(destroy_times) if destroy_times else 0,
        "total_median": median(total_times) if total_times else 0,
        "success_count": len(total_times),
    }


async def test_concurrent_warm(
    provider, provider_name: str, config: SandboxConfig, concurrency: int = 3
) -> dict:
    """Test concurrent sandbox creation to see if there's shared warm state."""
    print(f"\n⚡ Testing CONCURRENT creation for {provider_name} ({concurrency} concurrent)")

    async def create_execute_destroy(index: int):
        start_total = time.time()
        sandbox_id: str | None = None
        create_time = 0.0
        execute_time = 0.0
        destroy_time = 0.0
        error = None

        try:
            # Create
            start = time.time()
            sandbox = await provider.create_sandbox(config)
            sandbox_id = sandbox.id
            create_time = (time.time() - start) * 1000

            # Execute
            start = time.time()
            await provider.execute_command(sandbox_id, f"echo 'concurrent test {index}'")
            execute_time = (time.time() - start) * 1000
        except Exception as e:
            error = str(e)
        finally:
            if sandbox_id:
                start = time.time()
                try:
                    await provider.destroy_sandbox(sandbox_id)
                    destroy_time = (time.time() - start) * 1000
                except Exception as cleanup_error:
                    cleanup_message = f"cleanup failed: {cleanup_error}"
                    error = f"{error} | {cleanup_message}" if error else cleanup_message

        total_time = (time.time() - start_total) * 1000

        return {
            "index": index,
            "success": error is None,
            "create": create_time,
            "execute": execute_time,
            "destroy": destroy_time,
            "total": total_time,
            "error": error,
        }

    # Launch concurrent tasks
    start_all = time.time()
    tasks = [create_execute_destroy(i) for i in range(concurrency)]
    results = await asyncio.gather(*tasks)
    elapsed_all = (time.time() - start_all) * 1000

    for result in results:
        if result["success"]:
            print(
                f"   Concurrent {result['index']}: Create={result['create']:.0f}ms "
                f"Execute={result['execute']:.0f}ms Destroy={result['destroy']:.0f}ms "
                f"Total={result['total']:.0f}ms"
            )
        else:
            print(f"   Concurrent {result['index']}: ❌ Failed - {str(result['error'])[:80]}")

    successful_results = [r for r in results if r["success"]]
    create_times = [r["create"] for r in successful_results]
    execute_times = [r["execute"] for r in successful_results]
    total_times = [r["total"] for r in successful_results]
    efficiency = (sum(total_times) / elapsed_all) if elapsed_all > 0 and total_times else 0

    print(f"   Wall clock time: {elapsed_all:.0f}ms")
    if total_times:
        print(f"   Avg individual: {mean(total_times):.0f}ms")
        print(f"   Efficiency: {efficiency:.1f}x")
    else:
        print("   Avg individual: n/a")
        print("   Efficiency: n/a")

    return {
        "create_times": create_times,
        "execute_times": execute_times,
        "create_median": median(create_times) if create_times else 0,
        "execute_median": median(execute_times) if execute_times else 0,
        "wall_clock": elapsed_all,
        "efficiency": efficiency,
        "success_count": len(successful_results),
    }


async def test_provider_warmup_patterns(provider_name: str, display_name: str, provider_class):
    """Test complete warmup patterns for a provider."""
    print(f"\n{'='*80}")
    print(f"🔬 WARMUP ANALYSIS: {display_name}")
    print(f"{'='*80}")

    try:
        # Initialize fresh provider
        provider = provider_class()

        config = SandboxConfig(labels={"test": "warmup"})
        runtime_image = benchmark_image_for_provider(provider_name)
        if runtime_image:
            config.image = runtime_image

        # Test 1: Cold startup
        cold_results = await test_cold_startup(provider, display_name, config)

        # Small delay to ensure cold/warm separation
        await asyncio.sleep(2)

        # Test 2: Warm startup sequence
        warm_results = await test_warm_startup(provider, display_name, config, iterations=5)
        if not warm_results["success_count"]:
            print(f"❌ No successful warm startup runs for {display_name}")
            return None

        # Small delay
        await asyncio.sleep(1)

        # Test 3: Concurrent warmup
        concurrent_results = await test_concurrent_warm(
            provider, display_name, config, concurrency=3
        )

        # Analysis
        print(f"\n📊 WARMUP ANALYSIS FOR {display_name}")
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
        create_variance = 0
        execute_variance = 0
        if len(warm_results["create_times"]) > 1:
            create_variance = max(warm_results["create_times"]) - min(warm_results["create_times"])
        if len(warm_results["execute_times"]) > 1:
            execute_variance = max(warm_results["execute_times"]) - min(
                warm_results["execute_times"]
            )

        print("\nWarm Performance Stability:")
        print(f"  Create variance: {create_variance:.0f}ms")
        print(f"  Execute variance: {execute_variance:.0f}ms")

        # Concurrency analysis
        print("\nConcurrency Efficiency:")
        print(f"  Sequential warm: {warm_create:.0f}ms create")
        print(f"  Concurrent avg:  {concurrent_results['create_median']:.0f}ms create")
        print(f"  Concurrency efficiency: {concurrent_results['efficiency']:.1f}x")

        return {
            "provider": display_name,
            "cold": cold_results,
            "warm": warm_results,
            "concurrent": concurrent_results,
            "speedup": speedup,
            "create_speedup": create_speedup,
            "execute_speedup": execute_speedup,
        }

    except Exception as e:
        print(f"❌ Error testing {display_name}: {e}")
        return None


async def main():
    """Run warmup analysis for all providers."""
    print("🔥 COLD VS WARM STARTUP ANALYSIS")
    print("=" * 80)
    print("Testing startup patterns across providers...")

    providers_to_test = discover_benchmark_providers(include_cloudflare=False)

    results = []
    for provider in providers_to_test:
        provider_class = provider.load_class()
        result = await test_provider_warmup_patterns(
            provider.name,
            provider.display_name,
            provider_class,
        )
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
