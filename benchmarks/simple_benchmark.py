#!/usr/bin/env python
"""Simple benchmark for Modal provider."""

import asyncio
import os
import sys
import time
from statistics import mean, median, stdev

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandboxes import SandboxConfig
from sandboxes.providers.modal import ModalProvider


async def benchmark_modal(runs=5):
    """Benchmark Modal provider operations."""
    provider = ModalProvider()

    create_times = []
    execute_times = []
    destroy_times = []
    total_times = []

    print(f"\n🔬 Running Modal Benchmark ({runs} iterations)")
    print("=" * 60)

    for i in range(runs):
        print(f"\nRun {i+1}/{runs}:")

        # Total operation time
        total_start = time.time()

        # Create sandbox
        start = time.time()
        config = SandboxConfig(labels={"benchmark": f"run_{i}", "test": "modal_perf"})
        sandbox = await provider.create_sandbox(config)
        create_time = (time.time() - start) * 1000
        create_times.append(create_time)
        print(f"  ✅ Create: {create_time:.2f}ms - {sandbox.id}")

        # Execute command
        start = time.time()
        result = await provider.execute_command(
            sandbox.id,
            'python3 -c \'import sys; print(f"Python {sys.version}"); print("Benchmark test complete")\'',
        )
        execute_time = (time.time() - start) * 1000
        execute_times.append(execute_time)
        print(f"  ✅ Execute: {execute_time:.2f}ms - Success: {result.success}")

        # Destroy sandbox
        start = time.time()
        await provider.destroy_sandbox(sandbox.id)
        destroy_time = (time.time() - start) * 1000
        destroy_times.append(destroy_time)
        print(f"  ✅ Destroy: {destroy_time:.2f}ms")

        total_time = (time.time() - total_start) * 1000
        total_times.append(total_time)
        print(f"  ⏱️ Total: {total_time:.2f}ms")

        # Small delay between runs
        if i < runs - 1:
            await asyncio.sleep(1)

    print("\n" + "=" * 60)
    print("📊 RESULTS SUMMARY")
    print("=" * 60)

    # Calculate statistics
    def print_stats(name, times):
        if len(times) > 1:
            print(f"\n{name}:")
            print(f"  Mean:   {mean(times):.2f}ms")
            print(f"  Median: {median(times):.2f}ms")
            print(f"  Min:    {min(times):.2f}ms")
            print(f"  Max:    {max(times):.2f}ms")
            if len(times) > 2:
                print(f"  StdDev: {stdev(times):.2f}ms")
        else:
            print(f"\n{name}: {times[0]:.2f}ms")

    print_stats("CREATE SANDBOX", create_times)
    print_stats("EXECUTE COMMAND", execute_times)
    print_stats("DESTROY SANDBOX", destroy_times)
    print_stats("TOTAL OPERATION", total_times)

    print("\n" + "=" * 60)
    print(f"🎯 AVERAGE THROUGHPUT: {1000 / mean(total_times):.2f} ops/sec")
    print("=" * 60)

    return {
        "create": {"times": create_times, "mean": mean(create_times)},
        "execute": {"times": execute_times, "mean": mean(execute_times)},
        "destroy": {"times": destroy_times, "mean": mean(destroy_times)},
        "total": {"times": total_times, "mean": mean(total_times)},
    }


if __name__ == "__main__":
    results = asyncio.run(benchmark_modal(5))
