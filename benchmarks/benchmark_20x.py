#!/usr/bin/env python
"""Run comprehensive 20-run concurrent benchmark with verification."""

import asyncio
import os
import sys
import time
from statistics import mean, median, quantiles, stdev

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmarks.provider_matrix import benchmark_image_for_provider, discover_benchmark_providers
from sandboxes import SandboxConfig


async def verify_and_benchmark(
    provider_name: str,
    display_name: str,
    provider_class,
    runs: int = 20,
    concurrency: int = 20,
):
    """Benchmark provider with verification."""
    print(f"\n{'='*80}")
    print(f"{display_name} - {runs} RUNS ({concurrency} CONCURRENT)")
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
    except Exception:
        initial_sandboxes = []
        print("   Could not list sandboxes")

    runtime_image = benchmark_image_for_provider(provider_name)
    run_concurrency = max(1, min(concurrency, runs))
    semaphore = asyncio.Semaphore(run_concurrency)

    async def run_once(index: int) -> dict:
        run_result = {
            "index": index,
            "success": False,
            "sandbox_id": None,
            "create_time": 0.0,
            "execute_time": 0.0,
            "destroy_time": 0.0,
            "total_time": 0.0,
            "error": None,
        }

        total_start = time.time()
        sandbox_id: str | None = None

        try:
            config = SandboxConfig(
                labels={"benchmark": f"{provider_name}_20x", "run": str(index + 1)}
            )
            if runtime_image:
                config.image = runtime_image

            start = time.time()
            sandbox = await provider.create_sandbox(config)
            sandbox_id = sandbox.id
            run_result["sandbox_id"] = sandbox_id
            run_result["create_time"] = (time.time() - start) * 1000

            start = time.time()
            exec_result = await provider.execute_command(
                sandbox_id, "python3 -c 'import sys; print(f\"Python {sys.version.split()[0]}\")'"
            )
            run_result["execute_time"] = (time.time() - start) * 1000

            if exec_result.success:
                run_result["success"] = True
            else:
                run_result["error"] = f"Command failed (exit_code={exec_result.exit_code})"
        except Exception as e:
            run_result["error"] = str(e)
        finally:
            if sandbox_id:
                start = time.time()
                try:
                    await provider.destroy_sandbox(sandbox_id)
                    run_result["destroy_time"] = (time.time() - start) * 1000
                except Exception as cleanup_error:
                    run_result["success"] = False
                    cleanup_message = f"Cleanup failed: {cleanup_error}"
                    if run_result["error"]:
                        run_result["error"] = f"{run_result['error']} | {cleanup_message}"
                    else:
                        run_result["error"] = cleanup_message

            run_result["total_time"] = (time.time() - total_start) * 1000

        return run_result

    async def run_with_limit(index: int) -> dict:
        async with semaphore:
            return await run_once(index)

    print(f"\n🚀 Starting {runs} benchmark runs with concurrency={run_concurrency}...")
    print("-" * 60)
    start_wall = time.time()
    tasks = [asyncio.create_task(run_with_limit(i)) for i in range(runs)]
    run_results = []
    for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
        run_result = await task
        run_results.append(run_result)

        if completed % 5 == 0 or completed == runs:
            successful_so_far = [r for r in run_results if r["success"]]
            print(f"\n✅ Completed {completed}/{runs} runs...")
            print(
                "   Created sandboxes so far: " f"{sum(1 for r in run_results if r['sandbox_id'])}"
            )
            if successful_so_far:
                print(
                    "   Average create time: "
                    f"{mean([r['create_time'] for r in successful_so_far]):.0f}ms"
                )
                print(
                    "   Average total time: "
                    f"{mean([r['total_time'] for r in successful_so_far]):.0f}ms"
                )
            else:
                print("   Average create time: n/a")
                print("   Average total time: n/a")
            print("-" * 60)

    elapsed_wall = (time.time() - start_wall) * 1000

    sorted_results = sorted(run_results, key=lambda r: r["index"])
    for run_result in sorted_results:
        index = run_result["index"]
        if index < 3 or index >= runs - 2:
            if run_result["success"]:
                sandbox_id = run_result["sandbox_id"] or "unknown"
                print(
                    f"Run {index+1:2d}: Create={run_result['create_time']:6.0f}ms "
                    f"Execute={run_result['execute_time']:6.0f}ms "
                    f"Destroy={run_result['destroy_time']:6.0f}ms "
                    f"Total={run_result['total_time']:6.0f}ms [{str(sandbox_id)[:20]}...]"
                )
            else:
                print(f"Run {index+1:2d}: ❌ Failed - {str(run_result['error'])[:70]}")

    # Verify final sandbox count
    print("\n📊 Post-benchmark verification:")
    try:
        final_sandboxes = await provider.list_sandboxes()
        print(f"   Final sandbox count: {len(final_sandboxes)}")
        print(f"   Net change: {len(final_sandboxes) - len(initial_sandboxes)}")
    except Exception:
        print("   Could not verify final count")

    successful_runs = [r for r in run_results if r["success"]]
    failed_runs = runs - len(successful_runs)

    create_times = [r["create_time"] for r in successful_runs]
    execute_times = [r["execute_time"] for r in successful_runs]
    destroy_times = [r["destroy_time"] for r in successful_runs]
    total_times = [r["total_time"] for r in successful_runs]
    created_ids = [r["sandbox_id"] for r in run_results if r["sandbox_id"]]

    if not total_times:
        print(f"\n❌ All runs failed for {display_name}")
        return None

    # Calculate comprehensive statistics
    print(f"\n📈 STATISTICS FOR {display_name} ({len(total_times)}/{runs} successful)")
    print("=" * 60)

    def print_detailed_stats(name: str, times: list[float]):
        if not times:
            print(f"\n{name}:")
            print("  No successful samples")
            return

        print(f"\n{name}:")
        print(f"  Count:    {len(times)} samples")
        print(f"  Mean:     {mean(times):8.1f}ms")
        print(f"  Median:   {median(times):8.1f}ms")
        print(f"  Min:      {min(times):8.1f}ms")
        print(f"  Max:      {max(times):8.1f}ms")
        if len(times) > 1:
            q = quantiles(times, n=4)  # Quartiles
            print(f"  Q1:       {q[0]:8.1f}ms")
            print(f"  Q3:       {q[2]:8.1f}ms")
            print(f"  StdDev:   {stdev(times):8.1f}ms")
            if mean(times) > 0:
                print(f"  CV:       {(stdev(times)/mean(times)*100):8.1f}%")

    print_detailed_stats("CREATE", create_times)
    print_detailed_stats("EXECUTE", execute_times)
    print_detailed_stats("DESTROY", destroy_times)
    print_detailed_stats("TOTAL", total_times)

    print("\n📦 SANDBOX TRACKING:")
    print(f"  Sandboxes created: {len(created_ids)}")
    print(f"  Failed runs: {failed_runs}")
    print(f"  Success rate: {(len(total_times)/runs)*100:.1f}%")
    print(f"  Sample IDs: {created_ids[:3] if created_ids else 'None'}")
    print(f"  Wall clock for all runs: {elapsed_wall:.0f}ms")

    return {
        "name": display_name,
        "runs": runs,
        "successful": len(total_times),
        "failed": failed_runs,
        "create_median": median(create_times),
        "execute_median": median(execute_times),
        "destroy_median": median(destroy_times),
        "total_median": median(total_times),
        "throughput": len(total_times) / (elapsed_wall / 1000) if elapsed_wall > 0 else 0,
    }


async def main():
    """Run 20-run concurrent benchmark for all providers."""
    print("🔬 COMPREHENSIVE BENCHMARK - 20 RUNS PER PROVIDER")
    print("=" * 80)
    provider_specs = discover_benchmark_providers(include_cloudflare=False)
    runs = 20
    concurrency = int(os.getenv("BENCHMARK_20X_CONCURRENCY", str(runs)))
    estimated_sandboxes = len(provider_specs) * runs
    print(f"This will create and destroy up to {estimated_sandboxes} sandboxes total.")
    print(f"Per-provider concurrency: {concurrency}")
    print("Estimated time: provider-dependent")

    results = []

    if not provider_specs:
        print("\n❌ No configured providers found.")
        return

    # Test each provider
    for provider in provider_specs:
        provider_class = provider.load_class()
        result = await verify_and_benchmark(
            provider.name,
            provider.display_name,
            provider_class,
            runs=runs,
            concurrency=concurrency,
        )
        if result:
            results.append(result)

        # Delay between providers
        await asyncio.sleep(2)

    # Final comparison
    if results:
        print("\n" + "=" * 80)
        print("🏆 FINAL COMPARISON (20 runs each)")
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
