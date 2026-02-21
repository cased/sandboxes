#!/usr/bin/env python
"""Quick benchmark smoke test for configured providers."""

import asyncio
import os
import sys
import time
from statistics import mean, median

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmarks.provider_matrix import benchmark_image_for_provider, discover_benchmark_providers
from sandboxes import SandboxConfig


async def benchmark_provider(
    provider_name: str, display_name: str, provider_class, runs: int = 3
) -> dict | None:
    """Run a quick create/exec/destroy smoke benchmark for a provider."""
    try:
        provider = provider_class()
    except Exception as e:
        print(f"\n❌ {display_name} initialization failed: {e}")
        return None

    create_times = []
    execute_times = []
    destroy_times = []
    total_times = []

    print(f"\n🔬 Running {display_name} benchmark ({runs} iterations)")
    print("=" * 60)

    for i in range(runs):
        total_start = time.time()
        sandbox_id: str | None = None
        try:
            config = SandboxConfig(
                labels={"benchmark": "simple", "provider": provider_name, "run": str(i)}
            )
            runtime_image = benchmark_image_for_provider(provider_name)
            if runtime_image:
                config.image = runtime_image

            start = time.time()
            sandbox = await provider.create_sandbox(config)
            sandbox_id = sandbox.id
            create_time = (time.time() - start) * 1000
            create_times.append(create_time)

            start = time.time()
            result = await provider.execute_command(
                sandbox.id,
                "python3 -c 'import sys; print(sys.version.split()[0])'",
            )
            execute_time = (time.time() - start) * 1000
            execute_times.append(execute_time)

            start = time.time()
            await provider.destroy_sandbox(sandbox_id)
            sandbox_id = None
            destroy_time = (time.time() - start) * 1000
            destroy_times.append(destroy_time)

            total_time = (time.time() - total_start) * 1000
            total_times.append(total_time)

            icon = "✅" if result.success else "❌"
            print(
                f"Run {i+1}: {icon} Create={create_time:.0f}ms "
                f"Execute={execute_time:.0f}ms Destroy={destroy_time:.0f}ms Total={total_time:.0f}ms"
            )
        except Exception as e:
            print(f"Run {i+1}: ❌ Failed - {str(e)[:100]}")
        finally:
            if sandbox_id:
                try:
                    await provider.destroy_sandbox(sandbox_id)
                    print(f"Run {i+1}: ⚠️  Cleanup succeeded after failure")
                except Exception as cleanup_error:
                    print(f"Run {i+1}: ⚠️  Cleanup failed - {str(cleanup_error)[:100]}")

        if i < runs - 1:
            await asyncio.sleep(0.2)

    if not total_times:
        return None

    return {
        "provider": display_name,
        "runs": len(total_times),
        "create_median": median(create_times),
        "execute_median": median(execute_times),
        "destroy_median": median(destroy_times),
        "total_mean": mean(total_times),
        "total_median": median(total_times),
    }


async def main():
    """Run quick benchmark for configured providers."""
    providers = discover_benchmark_providers(include_cloudflare=False)
    if not providers:
        print("❌ No configured providers found.")
        return

    results = []
    for provider in providers:
        provider_class = provider.load_class()
        result = await benchmark_provider(
            provider.name, provider.display_name, provider_class, runs=3
        )
        if result:
            results.append(result)

    if not results:
        print("\n❌ No successful provider runs.")
        return

    print("\n" + "=" * 80)
    print("QUICK BENCHMARK SUMMARY")
    print("=" * 80)
    print(
        f"{'Provider':<12} {'Runs':<6} {'Create':<10} {'Execute':<10} {'Destroy':<10} {'Total':<10}"
    )
    print("-" * 80)
    for result in sorted(results, key=lambda r: r["total_median"]):
        print(
            f"{result['provider']:<12} "
            f"{result['runs']:<6} "
            f"{result['create_median']:<10.0f} "
            f"{result['execute_median']:<10.0f} "
            f"{result['destroy_median']:<10.0f} "
            f"{result['total_median']:<10.0f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
