#!/usr/bin/env python3
"""
Meta-benchmark runner that executes all benchmarks multiple times.

Runs each benchmark 10 times, aggregates results, and calculates statistics
including p50, p95, and p99 percentiles.

Outputs comprehensive results to benchmarks/results.txt
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from statistics import mean, median, quantiles, stdev
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


BENCHMARKS = [
    {
        "name": "Comprehensive Benchmark",
        "script": "comprehensive_benchmark.py",
        "runs": 2,  # Each run already does multiple iterations internally
        "description": "Apples-to-apples comparison with realistic workloads",
    },
    {
        "name": "Lifecycle Breakdown",
        "script": "compare_providers.py",
        "runs": 5,
        "description": "Create/execute/destroy timing breakdown",
    },
    {
        "name": "Cold vs Warm Start",
        "script": "cold_vs_warm.py",
        "runs": 3,
        "description": "Cold start vs warm start performance",
    },
    {
        "name": "Simple Benchmark",
        "script": "simple_benchmark.py",
        "runs": 5,
        "description": "Quick validation test",
    },
    {
        "name": "Concurrent Execution (20x)",
        "script": "benchmark_20x.py",
        "runs": 3,
        "description": "Concurrent sandbox operations",
    },
    {
        "name": "Image Reuse",
        "script": "image_reuse.py",
        "runs": 3,
        "description": "Image caching performance",
    },
]


def calculate_percentiles(data: list[float]) -> dict[str, float]:
    """Calculate p50, p95, p99 percentiles."""
    if not data:
        return {"p50": 0, "p95": 0, "p99": 0}

    if len(data) == 1:
        return {"p50": data[0], "p95": data[0], "p99": data[0]}

    # quantiles() needs at least 2 data points
    try:
        # quantiles(data, n=100) gives 99 cut points for percentiles
        percs = quantiles(data, n=100)
        return {
            "p50": median(data),
            "p95": percs[94] if len(percs) > 94 else max(data),  # 95th percentile
            "p99": percs[98] if len(percs) > 98 else max(data),  # 99th percentile
        }
    except Exception:
        # Fallback for small datasets
        return {
            "p50": median(data),
            "p95": max(data),
            "p99": max(data),
        }


def run_benchmark(script: str, run_number: int) -> dict[str, Any]:
    """Run a single benchmark and capture output."""
    script_path = Path(__file__).parent / script

    print(f"    Run {run_number}...", end=" ", flush=True)

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )
        duration = time.time() - start

        success = result.returncode == 0
        print(f"{'✓' if success else '✗'} ({duration:.1f}s)")

        return {
            "run": run_number,
            "success": success,
            "duration_seconds": duration,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        print(f"✗ TIMEOUT ({duration:.1f}s)")
        return {
            "run": run_number,
            "success": False,
            "duration_seconds": duration,
            "stdout": "",
            "stderr": "Benchmark timed out after 10 minutes",
            "exit_code": -1,
        }
    except Exception as e:
        duration = time.time() - start
        print(f"✗ ERROR ({str(e)})")
        return {
            "run": run_number,
            "success": False,
            "duration_seconds": duration,
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
        }


def main():
    """Run all benchmarks and generate comprehensive report."""
    print("=" * 80)
    print("COMPREHENSIVE BENCHMARK SUITE")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Running {len(BENCHMARKS)} benchmark suites")
    print("=" * 80)

    all_results = []
    overall_start = time.time()

    for bench_config in BENCHMARKS:
        print(f"\n📊 {bench_config['name']}")
        print(f"   {bench_config['description']}")
        print(f"   Running {bench_config['runs']} times...")

        bench_results = {
            "name": bench_config["name"],
            "script": bench_config["script"],
            "description": bench_config["description"],
            "runs": [],
        }

        for i in range(1, bench_config["runs"] + 1):
            result = run_benchmark(bench_config["script"], i)
            bench_results["runs"].append(result)

        # Calculate statistics
        successful_runs = [r for r in bench_results["runs"] if r["success"]]
        failed_runs = len(bench_results["runs"]) - len(successful_runs)

        if successful_runs:
            durations = [r["duration_seconds"] for r in successful_runs]
            bench_results["stats"] = {
                "success_count": len(successful_runs),
                "failure_count": failed_runs,
                "mean_duration": mean(durations),
                "median_duration": median(durations),
                "min_duration": min(durations),
                "max_duration": max(durations),
                "stddev": stdev(durations) if len(durations) > 1 else 0,
                "percentiles": calculate_percentiles(durations),
            }
        else:
            bench_results["stats"] = {
                "success_count": 0,
                "failure_count": failed_runs,
                "mean_duration": 0,
                "median_duration": 0,
                "min_duration": 0,
                "max_duration": 0,
                "stddev": 0,
                "percentiles": {"p50": 0, "p95": 0, "p99": 0},
            }

        all_results.append(bench_results)

        # Print summary
        if successful_runs:
            stats = bench_results["stats"]
            print(f"   Summary: {stats['success_count']}/{bench_config['runs']} successful")
            print(
                f"   Duration: {stats['mean_duration']:.1f}s avg, "
                f"{stats['median_duration']:.1f}s median, "
                f"±{stats['stddev']:.1f}s"
            )
        else:
            print("   ✗ All runs failed")

    overall_duration = time.time() - overall_start

    # Generate comprehensive report
    print("\n" + "=" * 80)
    print("GENERATING REPORT")
    print("=" * 80)

    output_path = Path(__file__).parent / "results.txt"

    with open(output_path, "w") as f:
        # Header
        f.write("=" * 80 + "\n")
        f.write("COMPREHENSIVE BENCHMARK RESULTS\n")
        f.write("=" * 80 + "\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total Duration: {overall_duration:.1f}s ({overall_duration/60:.1f}m)\n")
        f.write(f"Benchmarks: {len(BENCHMARKS)}\n")
        f.write("=" * 80 + "\n\n")

        # Executive Summary
        f.write("EXECUTIVE SUMMARY\n")
        f.write("-" * 80 + "\n\n")

        for result in all_results:
            stats = result["stats"]
            f.write(f"{result['name']}\n")
            f.write(
                f"  Success Rate: {stats['success_count']}/{stats['success_count'] + stats['failure_count']}\n"
            )
            if stats["success_count"] > 0:
                f.write(
                    f"  Duration: {stats['mean_duration']:.2f}s avg "
                    f"(p50={stats['percentiles']['p50']:.2f}s, "
                    f"p95={stats['percentiles']['p95']:.2f}s, "
                    f"p99={stats['percentiles']['p99']:.2f}s)\n"
                )
            f.write("\n")

        f.write("\n" + "=" * 80 + "\n\n")

        # Detailed results for each benchmark
        for result in all_results:
            f.write("=" * 80 + "\n")
            f.write(f"{result['name']}\n")
            f.write("=" * 80 + "\n")
            f.write(f"Script: {result['script']}\n")
            f.write(f"Description: {result['description']}\n")
            f.write(f"Runs: {len(result['runs'])}\n\n")

            # Statistics
            stats = result["stats"]
            f.write("STATISTICS\n")
            f.write("-" * 40 + "\n")
            f.write(
                f"Success: {stats['success_count']}/{stats['success_count'] + stats['failure_count']}\n"
            )
            if stats["success_count"] > 0:
                f.write(f"Mean:    {stats['mean_duration']:.3f}s\n")
                f.write(f"Median:  {stats['median_duration']:.3f}s\n")
                f.write(f"Std Dev: {stats['stddev']:.3f}s\n")
                f.write(f"Min:     {stats['min_duration']:.3f}s\n")
                f.write(f"Max:     {stats['max_duration']:.3f}s\n")
                f.write(f"P50:     {stats['percentiles']['p50']:.3f}s\n")
                f.write(f"P95:     {stats['percentiles']['p95']:.3f}s\n")
                f.write(f"P99:     {stats['percentiles']['p99']:.3f}s\n")
            f.write("\n")

            # Individual run details
            f.write("RUN DETAILS\n")
            f.write("-" * 40 + "\n")
            for run in result["runs"]:
                status = "✓" if run["success"] else "✗"
                f.write(f"Run {run['run']}: {status} ({run['duration_seconds']:.2f}s)\n")
            f.write("\n")

            # Raw output from first successful run
            successful_runs = [r for r in result["runs"] if r["success"]]
            if successful_runs:
                f.write("SAMPLE OUTPUT (First Successful Run)\n")
                f.write("-" * 40 + "\n")
                f.write(successful_runs[0]["stdout"])
                f.write("\n")

                if successful_runs[0]["stderr"]:
                    f.write("\nSTDERR:\n")
                    f.write(successful_runs[0]["stderr"])
                    f.write("\n")
            else:
                f.write("ERROR OUTPUT (First Failed Run)\n")
                f.write("-" * 40 + "\n")
                f.write("STDOUT:\n")
                f.write(result["runs"][0]["stdout"] or "(empty)")
                f.write("\n\nSTDERR:\n")
                f.write(result["runs"][0]["stderr"] or "(empty)")
                f.write("\n")

            f.write("\n" + "=" * 80 + "\n\n")

        # Final summary
        f.write("=" * 80 + "\n")
        f.write("CONCLUSIONS\n")
        f.write("=" * 80 + "\n\n")

        total_runs = sum(len(r["runs"]) for r in all_results)
        total_success = sum(r["stats"]["success_count"] for r in all_results)

        f.write(f"Total Runs: {total_runs}\n")
        f.write(f"Successful: {total_success}\n")
        f.write(f"Failed: {total_runs - total_success}\n")
        f.write(f"Success Rate: {total_success/total_runs*100:.1f}%\n\n")

        # Fastest benchmarks
        f.write("Benchmark Performance (by median duration):\n")
        sorted_results = sorted(
            [r for r in all_results if r["stats"]["success_count"] > 0],
            key=lambda x: x["stats"]["median_duration"],
        )
        for i, r in enumerate(sorted_results, 1):
            f.write(
                f"  {i}. {r['name']}: {r['stats']['median_duration']:.2f}s "
                f"(p95={r['stats']['percentiles']['p95']:.2f}s)\n"
            )

    print(f"\n✅ Report saved to: {output_path}")
    print(f"   Total duration: {overall_duration:.1f}s ({overall_duration/60:.1f}m)")
    print(f"   Total runs: {total_runs}, Success: {total_success}/{total_runs}")


if __name__ == "__main__":
    main()
