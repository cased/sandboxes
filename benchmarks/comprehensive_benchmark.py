#!/usr/bin/env python3
"""
Comprehensive benchmark for sandboxes library.
Tests E2B, Modal, and Daytona providers with various realistic workloads.

Features:
- Multiple test scenarios (Hello World, compute, I/O, package install)
- Apples-to-apples comparison with standardized images
- Statistical analysis with mean/stddev
- Detailed error reporting
- Auto-discovery of available providers

Based on ai-sandbox-benchmark (Apache 2.0 License)
https://github.com/nibzard/ai-sandbox-benchmark
"""
import asyncio
import os
import sys
import time
from pathlib import Path
from statistics import mean, median, quantiles, stdev
from typing import Any, Dict, List

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from tabulate import tabulate

    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False
    print("⚠️  Install tabulate for better output: pip install tabulate")

from sandboxes import run

# Standard image for apples-to-apples comparison (Modal/Daytona)
# This image includes Python 3.13, numpy, requests, and many AI/ML packages
# E2B uses their "code-interpreter" template (doesn't support arbitrary Docker images)
# code-interpreter includes Python, npm, Jupyter, numpy, pandas, matplotlib, etc.
STANDARD_IMAGE = "daytonaio/ai-test:0.2.3"


# Test scenarios - from simple to complex
TESTS = {
    "hello_world": {
        "name": "Hello World",
        "command": "echo 'Hello, World!'",
        "runs": 5,
        "description": "Simple shell command execution",
    },
    "prime_calculation": {
        "name": "Prime Calculation",
        "command": """python3 -c "
def is_prime(n):
    if n < 2: return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0: return False
    return True

primes = [n for n in range(2, 1000) if is_prime(n)]
print(f'Found {len(primes)} primes')
"
""",
        "runs": 5,
        "description": "CPU-bound computation",
    },
    "file_io": {
        "name": "File I/O (1000 files)",
        "command": """python3 -c "
import os
# Write 1000 small files
for i in range(1000):
    with open(f'/tmp/bench_{i}.txt', 'w') as f:
        f.write(f'Test file {i}' * 10)

# Read them back
total = 0
for i in range(1000):
    with open(f'/tmp/bench_{i}.txt', 'r') as f:
        total += len(f.read())

print(f'Processed {total} bytes')
"
""",
        "runs": 3,
        "description": "I/O performance test",
    },
    "package_install": {
        "name": "pip install requests",
        "command": "pip install -q requests && python3 -c 'import requests; print(f\"requests {requests.__version__}\")'",
        "runs": 2,
        "description": "Package installation speed (requests already installed in standard image)",
    },
    "numpy_fft": {
        "name": "NumPy FFT",
        "command": """python3 -c "
import numpy as np
x = np.random.random(10000)
result = np.fft.fft(x)
print(f'FFT: {len(result)} points')
"
""",
        "runs": 3,
        "description": "Numerical computation with pre-installed packages",
    },
}


async def benchmark_provider(
    provider_name: str, test_name: str, command: str, runs: int, use_standard_image: bool = True
) -> Dict[str, Any]:
    """Benchmark a single provider on a single test."""
    print(f"  [{provider_name}] Running {test_name}...")

    results = {
        "provider": provider_name,
        "test": test_name,
        "runs": [],
        "errors": 0,
    }

    for run_num in range(runs):
        try:
            start = time.time()

            # Use comparable images for fair comparison
            kwargs = {"provider": provider_name}
            if use_standard_image:
                if provider_name == "e2b":
                    # E2B uses templates, not Docker images - use their code-interpreter template
                    # Has Python, npm, Jupyter, and common ML packages (numpy, pandas, etc.)
                    kwargs["image"] = "code-interpreter"
                elif provider_name in ["modal", "daytona"]:
                    # Modal and Daytona can use Docker Hub images
                    kwargs["image"] = STANDARD_IMAGE

            result = await run(command, **kwargs)
            duration = (time.time() - start) * 1000  # Convert to ms

            results["runs"].append(
                {
                    "duration": duration,
                    "success": result.exit_code == 0,
                    "stdout": result.stdout[:100] if result.stdout else "",
                }
            )

            if result.exit_code != 0:
                results["errors"] += 1
                print(f"    Run {run_num + 1}/{runs}: FAILED ({duration:.2f}ms)")
                print(f"      stderr: {result.stderr[:200]}")
                print(f"      stdout: {result.stdout[:200]}")
            else:
                print(f"    Run {run_num + 1}/{runs}: {duration:.2f}ms")

        except Exception as e:
            results["errors"] += 1
            print(f"    Run {run_num + 1}/{runs}: ERROR - {str(e)[:100]}")
            results["runs"].append(
                {
                    "duration": 0,
                    "success": False,
                    "error": str(e),
                }
            )

    return results


async def run_benchmarks(providers: List[str], use_standard_image: bool = True):
    """Run all benchmarks for all providers."""
    print("\n" + "=" * 80)
    print("COMPREHENSIVE SANDBOX BENCHMARK")
    print("=" * 80)
    print(f"Testing providers: {', '.join(providers)}")
    print(f"Total tests: {len(TESTS)}")
    if use_standard_image:
        print(f"Modal/Daytona: {STANDARD_IMAGE}")
        print("E2B: code-interpreter template (Python, npm, Jupyter, ML packages)")
    print("=" * 80 + "\n")

    all_results = []

    for _test_id, test_config in TESTS.items():
        print(f"\n📊 Test: {test_config['name']}")
        print(f"   {test_config['description']}")
        print(f"   Runs: {test_config['runs']}")
        print()

        for provider in providers:
            result = await benchmark_provider(
                provider,
                test_config["name"],
                test_config["command"],
                test_config["runs"],
                use_standard_image=use_standard_image,
            )
            all_results.append(result)

    return all_results


def calculate_percentiles(data: List[float]) -> Dict[str, float]:
    """Calculate p50, p95, p99 percentiles."""
    if not data:
        return {"p50": 0, "p95": 0, "p99": 0}

    if len(data) == 1:
        return {"p50": data[0], "p95": data[0], "p99": data[0]}

    try:
        percs = quantiles(data, n=100)
        return {
            "p50": median(data),
            "p95": percs[94] if len(percs) > 94 else max(data),
            "p99": percs[98] if len(percs) > 98 else max(data),
        }
    except Exception:
        return {
            "p50": median(data),
            "p95": max(data),
            "p99": max(data),
        }


def generate_report(results: List[Dict[str, Any]]):
    """Generate a formatted benchmark report."""
    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS")
    print("=" * 80 + "\n")

    # Group by test
    by_test = {}
    for r in results:
        test = r["test"]
        if test not in by_test:
            by_test[test] = []
        by_test[test].append(r)

    for test_name, test_results in by_test.items():
        print(f"\n{'=' * 80}")
        print(f"Test: {test_name}")
        print("=" * 80)

        table_data = []
        for r in test_results:
            successful_runs = [run for run in r["runs"] if run["success"]]

            if successful_runs:
                durations = [run["duration"] for run in successful_runs]
                avg = mean(durations)
                std = stdev(durations) if len(durations) > 1 else 0
                min(durations)
                max(durations)
                percs = calculate_percentiles(durations)

                table_data.append(
                    [
                        r["provider"],
                        f"{avg:.2f}ms",
                        f"±{std:.2f}ms",
                        f"{percs['p50']:.2f}ms",
                        f"{percs['p95']:.2f}ms",
                        f"{percs['p99']:.2f}ms",
                        f"{len(successful_runs)}/{len(r['runs'])}",
                    ]
                )
            else:
                table_data.append(
                    [
                        r["provider"],
                        "FAILED",
                        "-",
                        "-",
                        "-",
                        "-",
                        f"0/{len(r['runs'])}",
                    ]
                )

        headers = ["Provider", "Avg Time", "Std Dev", "P50", "P95", "P99", "Success"]

        if HAS_TABULATE:
            print(tabulate(table_data, headers=headers, tablefmt="grid"))
        else:
            # Simple fallback formatting
            print(
                f"{headers[0]:<12} {headers[1]:<12} {headers[2]:<12} {headers[3]:<12} {headers[4]:<12} {headers[5]:<12} {headers[6]:<10}"
            )
            print("-" * 100)
            for row in table_data:
                print(
                    f"{row[0]:<12} {row[1]:<12} {row[2]:<12} {row[3]:<12} {row[4]:<12} {row[5]:<12} {row[6]:<10}"
                )

        # Show fastest provider
        valid_results = [r for r in test_results if any(run["success"] for run in r["runs"])]
        if valid_results:
            fastest = min(
                valid_results,
                key=lambda x: mean([run["duration"] for run in x["runs"] if run["success"]]),
            )
            fastest_time = mean([run["duration"] for run in fastest["runs"] if run["success"]])
            print(f"\n🏆 Fastest: {fastest['provider']} ({fastest_time:.2f}ms)")

    # Overall summary
    print("\n" + "=" * 80)
    print("OVERALL SUMMARY")
    print("=" * 80)

    # Count wins per provider
    wins = {}
    for test_name, test_results in by_test.items():
        valid_results = [r for r in test_results if any(run["success"] for run in r["runs"])]
        if valid_results:
            fastest = min(
                valid_results,
                key=lambda x: mean([run["duration"] for run in x["runs"] if run["success"]]),
            )
            wins[fastest["provider"]] = wins.get(fastest["provider"], 0) + 1

    if wins:
        print("\n🏆 Test Wins:")
        for provider, count in sorted(wins.items(), key=lambda x: -x[1]):
            print(f"  {provider}: {count}/{len(by_test)} tests")


async def main():
    """Main entry point."""
    # Check which providers are available
    print("Checking available providers...")

    providers_to_test = []

    # Check for API keys
    if os.getenv("E2B_API_KEY"):
        providers_to_test.append("e2b")
        print("✓ E2B configured")

    if os.getenv("MODAL_TOKEN_ID") or Path.home().joinpath(".modal.toml").exists():
        providers_to_test.append("modal")
        print("✓ Modal configured")

    if os.getenv("DAYTONA_API_KEY"):
        providers_to_test.append("daytona")
        print("✓ Daytona configured")

    if not providers_to_test:
        print("\n❌ No providers configured!")
        print("Set environment variables:")
        print("  - E2B_API_KEY for E2B")
        print("  - MODAL_TOKEN_ID for Modal (or run 'modal token set')")
        print("  - DAYTONA_API_KEY for Daytona")
        return

    # Run benchmarks
    results = await run_benchmarks(providers_to_test, use_standard_image=True)

    # Generate report
    generate_report(results)

    print("\n" + "=" * 80)
    print("BENCHMARK COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
