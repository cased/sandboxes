#!/usr/bin/env python
"""Benchmark to test image reuse vs fresh image pulls."""

import asyncio
import os
import sys
import time
from contextlib import suppress
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmarks.provider_matrix import (
    benchmark_image_for_provider,
    discover_benchmark_providers,
)
from sandboxes import SandboxConfig


async def test_same_image_reuse(
    provider, provider_name: str, image: str, iterations: int = 5
) -> dict:
    """Test creating multiple sandboxes with the same image."""
    print(f"\n🔄 Testing SAME IMAGE reuse: {image}")

    create_times = []
    execute_times = []
    destroy_times = []

    for i in range(iterations):
        config = SandboxConfig(image=image, labels={"test": "image_reuse", "iteration": str(i)})
        sandbox_id: str | None = None

        try:
            # Create
            start = time.time()
            sandbox = await provider.create_sandbox(config)
            sandbox_id = sandbox.id
            create_time = (time.time() - start) * 1000

            # Execute to test image readiness
            start = time.time()
            await provider.execute_command(sandbox_id, "python3 --version")
            execute_time = (time.time() - start) * 1000

            # Destroy
            start = time.time()
            await provider.destroy_sandbox(sandbox_id)
            sandbox_id = None
            destroy_time = (time.time() - start) * 1000

            create_times.append(create_time)
            execute_times.append(execute_time)
            destroy_times.append(destroy_time)

            print(f"   Run {i+1}: Create={create_time:.0f}ms Execute={execute_time:.0f}ms")
        except Exception as e:
            print(f"   Run {i+1}: ❌ Failed - {str(e)[:80]}")
        finally:
            if sandbox_id:
                try:
                    await provider.destroy_sandbox(sandbox_id)
                    print(f"   Run {i+1}: ⚠️  Cleanup succeeded after failure")
                except Exception as cleanup_error:
                    print(f"   Run {i+1}: ⚠️  Cleanup failed - {str(cleanup_error)[:80]}")

        # Small delay
        await asyncio.sleep(0.5)

    return {
        "image": image,
        "create_times": create_times,
        "execute_times": execute_times,
        "destroy_times": destroy_times,
        "create_median": median(create_times) if create_times else 0,
        "execute_median": median(execute_times) if execute_times else 0,
        "first_create": create_times[0] if create_times else 0,
        "subsequent_create_median": (
            median(create_times[1:])
            if len(create_times) > 1
            else (create_times[0] if create_times else 0)
        ),
        "success_count": len(create_times),
    }


async def test_different_images(provider, provider_name: str, images: list[str]) -> dict:
    """Test creating sandboxes with different images."""
    print("\n🆕 Testing DIFFERENT IMAGES")

    results = []

    for i, image in enumerate(images):
        config = SandboxConfig(
            image=image, labels={"test": "different_images", "image_index": str(i)}
        )

        print(f"   Testing image: {image}")
        sandbox_id: str | None = None

        # Create
        start = time.time()
        try:
            sandbox = await provider.create_sandbox(config)
            sandbox_id = sandbox.id
            create_time = (time.time() - start) * 1000

            # Execute to test image works
            start = time.time()
            result = await provider.execute_command(
                sandbox_id, "python3 --version || python --version || echo 'No Python'"
            )
            execute_time = (time.time() - start) * 1000

            # Destroy
            await provider.destroy_sandbox(sandbox_id)
            sandbox_id = None

            results.append(
                {
                    "image": image,
                    "create_time": create_time,
                    "execute_time": execute_time,
                    "success": result.success,
                    "output": result.stdout.strip()[:50],
                }
            )

            print(
                f"     Create: {create_time:.0f}ms, Execute: {execute_time:.0f}ms, Output: {result.stdout.strip()[:30]}"
            )

        except Exception as e:
            print(f"     ❌ Failed: {str(e)[:50]}")
            results.append(
                {
                    "image": image,
                    "create_time": 0,
                    "execute_time": 0,
                    "success": False,
                    "error": str(e)[:100],
                }
            )
        finally:
            if sandbox_id:
                with suppress(Exception):
                    await provider.destroy_sandbox(sandbox_id)

        # Delay between different images
        await asyncio.sleep(1)

    return results


async def test_concurrent_same_image(
    provider, provider_name: str, image: str, concurrency: int = 3
) -> dict:
    """Test concurrent sandboxes with same image."""
    print(f"\n⚡ Testing CONCURRENT same image: {image}")

    async def create_test_destroy(index: int):
        config = SandboxConfig(image=image, labels={"test": "concurrent_same", "index": str(index)})

        start_total = time.time()
        sandbox_id: str | None = None
        create_time = 0.0
        execute_time = 0.0
        error = None

        try:
            # Create
            start = time.time()
            sandbox = await provider.create_sandbox(config)
            sandbox_id = sandbox.id
            create_time = (time.time() - start) * 1000

            # Execute
            start = time.time()
            await provider.execute_command(sandbox_id, f"echo 'concurrent {index}'")
            execute_time = (time.time() - start) * 1000
        except Exception as e:
            error = str(e)
        finally:
            if sandbox_id:
                try:
                    await provider.destroy_sandbox(sandbox_id)
                except Exception as cleanup_error:
                    cleanup_message = f"cleanup failed: {cleanup_error}"
                    error = f"{error} | {cleanup_message}" if error else cleanup_message

        total_time = (time.time() - start_total) * 1000

        return {
            "index": index,
            "success": error is None,
            "create_time": create_time,
            "execute_time": execute_time,
            "total_time": total_time,
            "error": error,
        }

    # Launch concurrent tasks
    start_wall = time.time()
    tasks = [create_test_destroy(i) for i in range(concurrency)]
    results = await asyncio.gather(*tasks)
    wall_time = (time.time() - start_wall) * 1000

    successful_results = [r for r in results if r["success"]]
    create_times = [r["create_time"] for r in successful_results]

    for r in results:
        if r["success"]:
            print(
                f"   Concurrent {r['index']}: Create={r['create_time']:.0f}ms Execute={r['execute_time']:.0f}ms"
            )
        else:
            print(f"   Concurrent {r['index']}: ❌ Failed - {str(r['error'])[:80]}")

    print(f"   Wall clock: {wall_time:.0f}ms")
    if successful_results and wall_time > 0:
        efficiency = sum([r["total_time"] for r in successful_results]) / wall_time
        print(f"   Efficiency: {efficiency:.1f}x")
    else:
        efficiency = 0
        print("   Efficiency: n/a")

    return {
        "results": results,
        "create_times": create_times,
        "wall_time": wall_time,
        "create_median": median(create_times) if create_times else 0,
        "success_count": len(successful_results),
        "efficiency": efficiency,
    }


async def test_provider_image_patterns(provider_name: str, display_name: str, provider_class):
    """Test image reuse patterns for a provider."""
    print(f"\n{'='*80}")
    print(f"🖼️  IMAGE REUSE ANALYSIS: {display_name}")
    print(f"{'='*80}")

    try:
        provider = provider_class()

        # Provider-specific image configs
        if provider_name == "modal":
            primary_image = benchmark_image_for_provider(provider_name)
            test_images = [
                "python:3.11-slim",
                "python:3.12-slim",
                "python:3.10-slim",
                "ubuntu:22.04",
            ]
        elif provider_name in {"e2b", "daytona", "hopx"}:
            primary_image = benchmark_image_for_provider(provider_name)
            test_images = [primary_image] if primary_image else []
        else:
            return None

        results = {}

        # Test 1: Same image reuse
        if primary_image:
            same_image_results = await test_same_image_reuse(
                provider, display_name, primary_image, iterations=5
            )
            results["same_image"] = same_image_results

            # Test 2: Concurrent same image
            concurrent_results = await test_concurrent_same_image(
                provider, display_name, primary_image, concurrency=3
            )
            results["concurrent_same"] = concurrent_results

        # Test 3: Different images (Modal only for now)
        if provider_name == "modal":
            different_images_results = await test_different_images(
                provider, display_name, test_images
            )
            results["different_images"] = different_images_results

        # Analysis
        print(f"\n📊 IMAGE REUSE ANALYSIS FOR {display_name}")
        print(f"{'='*60}")

        if "same_image" in results:
            same = results["same_image"]
            if not same["success_count"]:
                print("\nSame Image Reuse:")
                print("  No successful runs")
                return results
            first_create = same["first_create"]
            subsequent_median = same["subsequent_create_median"]
            reuse_speedup = first_create / subsequent_median if subsequent_median > 0 else 1

            print(f"\nSame Image Reuse ({primary_image}):")
            print(f"  First create:      {first_create:.0f}ms")
            print(f"  Subsequent median: {subsequent_median:.0f}ms")
            print(f"  Reuse speedup:     {reuse_speedup:.2f}x")

            # Variance in subsequent creates
            if len(same["create_times"]) > 1:
                subsequent_times = same["create_times"][1:]
                variance = max(subsequent_times) - min(subsequent_times)
                print(f"  Reuse consistency: {variance:.0f}ms variance")

        if "concurrent_same" in results:
            concurrent = results["concurrent_same"]
            print("\nConcurrent Same Image:")
            print(f"  Concurrent median: {concurrent['create_median']:.0f}ms")
            if "same_image" in results:
                if concurrent["create_median"] > 0:
                    concurrent_vs_sequential = (
                        results["same_image"]["subsequent_create_median"]
                        / concurrent["create_median"]
                    )
                    print(f"  vs Sequential:     {concurrent_vs_sequential:.2f}x")
                else:
                    print("  vs Sequential:     n/a")

        if "different_images" in results:
            different = results["different_images"]
            successful = [r for r in different if r["success"]]
            if successful:
                create_times = [r["create_time"] for r in successful]
                print("\nDifferent Images:")
                print(f"  Avg create time:   {mean(create_times):.0f}ms")
                print(f"  Range:            {min(create_times):.0f}ms - {max(create_times):.0f}ms")

                for r in different:
                    status = "✅" if r["success"] else "❌"
                    image_short = r["image"].split(":")[0] if r["image"] else "default"
                    if r["success"]:
                        print(f"    {status} {image_short:15} {r['create_time']:6.0f}ms")
                    else:
                        print(
                            f"    {status} {image_short:15} Failed: {r.get('error', 'Unknown')[:30]}"
                        )

        return results

    except Exception as e:
        print(f"❌ Error testing {display_name}: {e}")
        return None


async def main():
    """Run image reuse analysis for all providers."""
    print("🖼️  IMAGE REUSE BENCHMARK")
    print("=" * 80)
    print("Testing image caching and reuse patterns...")

    providers_to_test = discover_benchmark_providers(
        include_cloudflare=False,
        image_only=True,
    )

    all_results = []
    for provider in providers_to_test:
        provider_class = provider.load_class()
        result = await test_provider_image_patterns(
            provider.name,
            provider.display_name,
            provider_class,
        )
        if result:
            all_results.append((provider.display_name, result))

        # Delay between providers
        await asyncio.sleep(3)

    # Final comparison
    if all_results:
        print(f"\n{'='*80}")
        print("🏆 IMAGE REUSE COMPARISON")
        print(f"{'='*80}")

        print(
            f"\n{'Provider':<10} {'First (ms)':<12} {'Reuse (ms)':<12} {'Speedup':<10} {'Consistency':<12}"
        )
        print("-" * 70)

        for name, results in all_results:
            if "same_image" in results:
                same = results["same_image"]
                if not same["success_count"]:
                    continue
                first = same["first_create"]
                reuse = same["subsequent_create_median"]
                speedup = first / reuse if reuse > 0 else 1

                if len(same["create_times"]) > 1:
                    subsequent_times = same["create_times"][1:]
                    variance = max(subsequent_times) - min(subsequent_times)
                    consistency = f"{variance:.0f}ms var"
                else:
                    consistency = "N/A"

                print(
                    f"{name:<10} {first:<12.0f} {reuse:<12.0f} {speedup:<10.2f} {consistency:<12}"
                )

        # Find best image reuse
        reuse_data = []
        for name, results in all_results:
            if "same_image" in results:
                same = results["same_image"]
                if not same["success_count"]:
                    continue
                speedup = (
                    same["first_create"] / same["subsequent_create_median"]
                    if same["subsequent_create_median"] > 0
                    else 1
                )
                reuse_data.append((name, speedup))

        if reuse_data:
            best_reuse = max(reuse_data, key=lambda x: x[1])
            print(f"\n🏆 Best image reuse: {best_reuse[0]} ({best_reuse[1]:.2f}x speedup)")


if __name__ == "__main__":
    asyncio.run(main())
