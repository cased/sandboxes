#!/usr/bin/env python
"""Run comprehensive 20-iteration benchmark with verification."""

import asyncio
import os
import sys
import time
from statistics import mean, median, quantiles, stdev

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandboxes import SandboxConfig
from sandboxes.providers.daytona import DaytonaProvider
from sandboxes.providers.e2b import E2BProvider
from sandboxes.providers.modal import ModalProvider


async def verify_and_benchmark(provider_class, name: str, runs: int = 20):
    """Benchmark provider with verification."""
    print(f"\n{'='*80}")
    print(f"4{name} - {runs} ITERATIONS")
    print(f"{'='*80}")

    try:
        provider = provider_class()
    except Exception as e:
        print(f"Failed to initialize: {e}")
        return None

    # First, list existing sandboxes to track creation
    print("\nPre-benchmark sandbox count:")
    try:
        initial_sandboxes = await provider.list_sandboxes()
        print(f"   Existing sandboxes: {len(initial_sandboxes)}")
    except:
        initial_sandboxes = []
        print("   Could not list sandboxes")

    # Track metrics
    create_times = []
    execute_times = []
    destroy_times = []
    total_times = []
    created_ids = []
    failed_runs = 0

    print(f"\n🚀 Starting {runs} benchmark runs...")
    print("-" * 60)

    for i in range(runs):
        # Show progress every 5 runs
        if i > 0 and i % 5 == 0:
            print(f"\n✅ Completed {i}/{runs} runs...")
            print(f"   Created sandboxes so far: {len(created_ids)}")
            print(f"   Average create time: {mean(create_times):.0f}ms")
            print(f"   Average total time: {mean(total_times):.0f}ms")
            print("-" * 60)

        total_start = time.time()

        try:
            # Create sandbox
            start = time.time()
            config = SandboxConfig(labels={"benchmark": f"{name.lower()}_20x", "run": str(i + 1)})
            if name == "Modal":
                config.provider_config = {"image": "python:3.11-slim"}

            sandbox = await provider.create_sandbox(config)
            create_time = (time.time() - start) * 1000
            create_times.append(create_time)
            created_ids.append(sandbox.id)

            # Execute command
            start = time.time()
            await provider.execute_command(
                sandbox.id, "python3 -c 'import sys; print(f\"Python {sys.version.split()[0]}\")'"
            )
            execute_time = (time.time() - start) * 1000
            execute_times.append(execute_time)

            # Destroy sandbox
            start = time.time()
            await provider.destroy_sandbox(sandbox.id)
            destroy_time = (time.time() - start) * 1000
            destroy_times.append(destroy_time)

            total_time = (time.time() - total_start) * 1000
            total_times.append(total_time)

            # Print individual run details for first 3 and last 2
            if i < 3 or i >= runs - 2:
                print(
                    f"Run {i+1:2d}: Create={create_time:6.0f}ms Execute={execute_time:6.0f}ms "
                    f"Destroy={destroy_time:6.0f}ms Total={total_time:6.0f}ms [{sandbox.id[:20]}...]"
                )

        except Exception as e:
            failed_runs += 1
            print(f"Run {i+1:2d}: ❌ Failed - {str(e)[:50]}")

        # Small delay between runs
        await asyncio.sleep(0.2)

    # Verify final sandbox count
    print("\n📊 Post-benchmark verification:")
    try:
        final_sandboxes = await provider.list_sandboxes()
        print(f"   Final sandbox count: {len(final_sandboxes)}")
        print(f"   Net change: {len(final_sandboxes) - len(initial_sandboxes)}")
    except:
        print("   Could not verify final count")

    if not create_times:
        print(f"\n❌ All runs failed for {name}")
        return None

    # Calculate comprehensive statistics
    print(f"\n📈 STATISTICS FOR {name} ({len(create_times)}/{runs} successful)")
    print("=" * 60)

    def print_detailed_stats(name, times):
        if len(times) > 1:
            q = quantiles(times, n=4)  # Quartiles
            print(f"\n{name}:")
            print(f"  Count:    {len(times)} samples")
            print(f"  Mean:     {mean(times):8.1f}ms")
            print(f"  Median:   {median(times):8.1f}ms")
            print(f"  Min:      {min(times):8.1f}ms")
            print(f"  Q1:       {q[0]:8.1f}ms")
            print(f"  Q3:       {q[2]:8.1f}ms")
            print(f"  Max:      {max(times):8.1f}ms")
            if len(times) > 2:
                print(f"  StdDev:   {stdev(times):8.1f}ms")
                print(
                    f"  CV:       {(stdev(times)/mean(times)*100):8.1f}%"
                )  # Coefficient of variation

    print_detailed_stats("CREATE", create_times)
    print_detailed_stats("EXECUTE", execute_times)
    print_detailed_stats("DESTROY", destroy_times)
    print_detailed_stats("TOTAL", total_times)

    print("\n📦 SANDBOX TRACKING:")
    print(f"  Sandboxes created: {len(created_ids)}")
    print(f"  Failed runs: {failed_runs}")
    print(f"  Success rate: {(len(created_ids)/runs)*100:.1f}%")
    print(f"  Sample IDs: {created_ids[:3] if created_ids else 'None'}")

    return {
        "name": name,
        "runs": runs,
        "successful": len(create_times),
        "failed": failed_runs,
        "create_median": median(create_times) if create_times else 0,
        "execute_median": median(execute_times) if execute_times else 0,
        "destroy_median": median(destroy_times) if destroy_times else 0,
        "total_median": median(total_times) if total_times else 0,
        "throughput": 1000 / median(total_times) if total_times else 0,
    }


async def main():
    """Run 20-iteration benchmark for all providers."""
    print("🔬 COMPREHENSIVE BENCHMARK - 20 ITERATIONS PER PROVIDER")
    print("=" * 80)
    print("This will create and destroy 60 sandboxes total.")
    print("Estimated time: 3-5 minutes")

    results = []

    # Test each provider
    for provider_class, name in [
        (ModalProvider, "Modal"),
        (E2BProvider, "E2B"),
        (DaytonaProvider, "Daytona"),
    ]:
        if name == "E2B" and not os.getenv("E2B_API_KEY"):
            print(f"\n⚠️ Skipping {name} - no API key")
            continue
        if name == "Daytona" and not os.getenv("DAYTONA_API_KEY"):
            print(f"\n⚠️ Skipping {name} - no API key")
            continue

        result = await verify_and_benchmark(provider_class, name, runs=20)
        if result:
            results.append(result)

        # Delay between providers
        await asyncio.sleep(2)

    # Final comparison
    if results:
        print("\n" + "=" * 80)
        print("🏆 FINAL COMPARISON (20 iterations each)")
        print("=" * 80)

        print(
            f"\n{'Provider':<10} {'Success':<10} {'Create':<12} {'Execute':<12} {'Destroy':<12} {'Total':<12} {'Throughput':<12}"
        )
        print("-" * 94)

        for r in sorted(results, key=lambda x: x["total_median"]):
            print(
                f"{r['name']:<10} "
                f"{r['successful']}/{r['runs']:<9} "
                f"{r['create_median']:<11.0f} "
                f"{r['execute_median']:<11.0f} "
                f"{r['destroy_median']:<11.0f} "
                f"{r['total_median']:<11.0f} "
                f"{r['throughput']:<11.2f}"
            )

        # Winner
        fastest = min(results, key=lambda x: x["total_median"])
        print(f"\n🥇 FASTEST: {fastest['name']} @ {fastest['total_median']:.0f}ms median")

        most_reliable = max(results, key=lambda x: x["successful"])
        if most_reliable["successful"] == most_reliable["runs"]:
            print(f"💯 MOST RELIABLE: {most_reliable['name']} (100% success rate)")


if __name__ == "__main__":
    asyncio.run(main())
