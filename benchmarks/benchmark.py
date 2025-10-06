#!/usr/bin/env python
"""Benchmark suite for comparing sandbox providers."""

import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandboxes import SandboxConfig
from sandboxes.providers.daytona import DaytonaProvider
from sandboxes.providers.e2b import E2BProvider
from sandboxes.providers.modal import ModalProvider


@dataclass
class BenchmarkResult:
    """Single benchmark result."""

    provider: str
    operation: str
    duration_ms: float
    success: bool
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ProviderMetrics:
    """Aggregated metrics for a provider."""

    provider: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float
    avg_duration_ms: float
    median_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float
    stddev_duration_ms: float
    operations: Dict[str, Dict[str, float]]


class SandboxBenchmark:
    """Benchmark suite for sandbox providers."""

    def __init__(self, iterations: int = 5):
        """Initialize benchmark suite."""
        self.iterations = iterations
        self.results: List[BenchmarkResult] = []
        self.providers = {}

        # Initialize available providers
        self._init_providers()

    def _init_providers(self):
        """Initialize available providers."""
        # E2B
        if os.getenv("E2B_API_KEY"):
            try:
                self.providers["e2b"] = E2BProvider()
                print("✅ E2B provider initialized")
            except Exception as e:
                print(f"❌ E2B provider failed: {e}")

        # Daytona
        if os.getenv("DAYTONA_API_KEY"):
            try:
                self.providers["daytona"] = DaytonaProvider()
                print("✅ Daytona provider initialized")
            except Exception as e:
                print(f"❌ Daytona provider failed: {e}")

        # Modal
        if os.path.exists(os.path.expanduser("~/.modal.toml")):
            try:
                self.providers["modal"] = ModalProvider()
                print("✅ Modal provider initialized")
            except Exception as e:
                print(f"❌ Modal provider failed: {e}")

    async def benchmark_create_sandbox(self, provider_name: str) -> List[BenchmarkResult]:
        """Benchmark sandbox creation."""
        provider = self.providers[provider_name]
        results = []

        for i in range(self.iterations):
            config = SandboxConfig(
                labels={"benchmark": "create", "iteration": str(i)},
                timeout_seconds=120,
            )

            start = time.time()
            try:
                sandbox = await provider.create_sandbox(config)
                duration_ms = (time.time() - start) * 1000

                results.append(
                    BenchmarkResult(
                        provider=provider_name,
                        operation="create_sandbox",
                        duration_ms=duration_ms,
                        success=True,
                        metadata={"sandbox_id": sandbox.id},
                    )
                )

                # Clean up
                await provider.destroy_sandbox(sandbox.id)

            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                results.append(
                    BenchmarkResult(
                        provider=provider_name,
                        operation="create_sandbox",
                        duration_ms=duration_ms,
                        success=False,
                        error=str(e),
                    )
                )

        return results

    async def benchmark_execute_command(self, provider_name: str) -> List[BenchmarkResult]:
        """Benchmark command execution."""
        provider = self.providers[provider_name]
        results = []

        # Create one sandbox for all iterations
        config = SandboxConfig(
            labels={"benchmark": "execute"},
            timeout_seconds=300,
        )

        try:
            sandbox = await provider.create_sandbox(config)

            # Test different commands
            commands = [
                ("echo 'Hello, World!'", "simple_echo"),
                ("python -c 'print(sum(range(1000)))'", "python_calculation"),
                ("for i in {1..10}; do echo $i; done", "bash_loop"),
                ("python -c 'import sys; print(sys.version)'", "python_version"),
                ("ls -la /", "filesystem_list"),
            ]

            for command, cmd_type in commands:
                for _i in range(self.iterations):
                    start = time.time()
                    try:
                        result = await provider.execute_command(sandbox.id, command)
                        duration_ms = (time.time() - start) * 1000

                        results.append(
                            BenchmarkResult(
                                provider=provider_name,
                                operation=f"execute_{cmd_type}",
                                duration_ms=duration_ms,
                                success=result.success,
                                metadata={"exit_code": result.exit_code},
                            )
                        )

                    except Exception as e:
                        duration_ms = (time.time() - start) * 1000
                        results.append(
                            BenchmarkResult(
                                provider=provider_name,
                                operation=f"execute_{cmd_type}",
                                duration_ms=duration_ms,
                                success=False,
                                error=str(e),
                            )
                        )

            # Clean up
            await provider.destroy_sandbox(sandbox.id)

        except Exception as e:
            print(f"Failed to create sandbox for execution benchmark: {e}")

        return results

    async def benchmark_reuse_sandbox(self, provider_name: str) -> List[BenchmarkResult]:
        """Benchmark sandbox reuse via labels."""
        provider = self.providers[provider_name]
        results = []

        labels = {"benchmark": "reuse", "session": "test123"}
        config = SandboxConfig(labels=labels, timeout_seconds=120)

        # Create initial sandbox
        start = time.time()
        try:
            sandbox = await provider.create_sandbox(config)
            create_duration = (time.time() - start) * 1000
            sandbox_id = sandbox.id

            results.append(
                BenchmarkResult(
                    provider=provider_name,
                    operation="create_for_reuse",
                    duration_ms=create_duration,
                    success=True,
                    metadata={"sandbox_id": sandbox_id},
                )
            )

            # Try to find and reuse
            for _i in range(self.iterations):
                start = time.time()
                try:
                    # Try to find existing sandbox
                    found = await provider.find_sandbox(labels)
                    find_duration = (time.time() - start) * 1000

                    if found and found.id == sandbox_id:
                        results.append(
                            BenchmarkResult(
                                provider=provider_name,
                                operation="find_and_reuse",
                                duration_ms=find_duration,
                                success=True,
                                metadata={"reused": True},
                            )
                        )
                    else:
                        results.append(
                            BenchmarkResult(
                                provider=provider_name,
                                operation="find_and_reuse",
                                duration_ms=find_duration,
                                success=False,
                                error="Sandbox not found for reuse",
                            )
                        )

                except Exception as e:
                    find_duration = (time.time() - start) * 1000
                    results.append(
                        BenchmarkResult(
                            provider=provider_name,
                            operation="find_and_reuse",
                            duration_ms=find_duration,
                            success=False,
                            error=str(e),
                        )
                    )

            # Clean up
            await provider.destroy_sandbox(sandbox_id)

        except Exception as e:
            results.append(
                BenchmarkResult(
                    provider=provider_name,
                    operation="create_for_reuse",
                    duration_ms=(time.time() - start) * 1000,
                    success=False,
                    error=str(e),
                )
            )

        return results

    async def run_provider_benchmark(self, provider_name: str) -> List[BenchmarkResult]:
        """Run all benchmarks for a single provider."""
        print(f"\n📊 Benchmarking {provider_name}...")
        all_results = []

        # Sandbox creation
        print(f"  Testing sandbox creation ({self.iterations} iterations)...")
        results = await self.benchmark_create_sandbox(provider_name)
        all_results.extend(results)

        # Command execution
        print("  Testing command execution...")
        results = await self.benchmark_execute_command(provider_name)
        all_results.extend(results)

        # Sandbox reuse
        print("  Testing sandbox reuse...")
        results = await self.benchmark_reuse_sandbox(provider_name)
        all_results.extend(results)

        return all_results

    def calculate_metrics(self, results: List[BenchmarkResult]) -> Dict[str, ProviderMetrics]:
        """Calculate aggregated metrics from results."""
        metrics = {}

        for provider_name in self.providers:
            provider_results = [r for r in results if r.provider == provider_name]

            if not provider_results:
                continue

            # Calculate overall metrics
            total_runs = len(provider_results)
            successful_runs = sum(1 for r in provider_results if r.success)
            failed_runs = total_runs - successful_runs

            # Calculate timing metrics (only for successful runs)
            successful_durations = [r.duration_ms for r in provider_results if r.success]

            if successful_durations:
                avg_duration = statistics.mean(successful_durations)
                median_duration = statistics.median(successful_durations)
                min_duration = min(successful_durations)
                max_duration = max(successful_durations)
                stddev_duration = (
                    statistics.stdev(successful_durations) if len(successful_durations) > 1 else 0
                )
            else:
                avg_duration = median_duration = min_duration = max_duration = stddev_duration = 0

            # Calculate per-operation metrics
            operations = {}
            for op_type in set(r.operation for r in provider_results):
                op_results = [r for r in provider_results if r.operation == op_type]
                op_durations = [r.duration_ms for r in op_results if r.success]

                if op_durations:
                    operations[op_type] = {
                        "count": len(op_results),
                        "success_rate": sum(1 for r in op_results if r.success) / len(op_results),
                        "avg_ms": statistics.mean(op_durations),
                        "median_ms": statistics.median(op_durations),
                        "min_ms": min(op_durations),
                        "max_ms": max(op_durations),
                    }

            metrics[provider_name] = ProviderMetrics(
                provider=provider_name,
                total_runs=total_runs,
                successful_runs=successful_runs,
                failed_runs=failed_runs,
                success_rate=successful_runs / total_runs if total_runs > 0 else 0,
                avg_duration_ms=avg_duration,
                median_duration_ms=median_duration,
                min_duration_ms=min_duration,
                max_duration_ms=max_duration,
                stddev_duration_ms=stddev_duration,
                operations=operations,
            )

        return metrics

    def print_results(self, metrics: Dict[str, ProviderMetrics]):
        """Print benchmark results in a nice format."""
        print("\n" + "=" * 80)
        print("🏆 BENCHMARK RESULTS")
        print("=" * 80)

        # Overall comparison
        print("\n📈 Overall Performance")
        print("-" * 40)
        print(f"{'Provider':<15} {'Success Rate':<15} {'Avg Time (ms)':<15} {'Median (ms)':<15}")
        print("-" * 40)

        for provider, m in metrics.items():
            print(
                f"{provider:<15} {m.success_rate*100:>6.1f}%        "
                f"{m.avg_duration_ms:>10.2f}     {m.median_duration_ms:>10.2f}"
            )

        # Per-operation breakdown
        print("\n📊 Operation Breakdown")
        print("-" * 40)

        for provider, m in metrics.items():
            print(f"\n{provider.upper()}:")
            for op, stats in sorted(m.operations.items()):
                print(
                    f"  {op:<30} "
                    f"Success: {stats['success_rate']*100:>5.1f}% "
                    f"Avg: {stats['avg_ms']:>8.2f}ms "
                    f"Med: {stats['median_ms']:>8.2f}ms "
                    f"Range: [{stats['min_ms']:.0f}-{stats['max_ms']:.0f}]ms"
                )

        # Winner determination
        print("\n🥇 Winners by Category")
        print("-" * 40)

        if metrics:
            # Fastest creation
            fastest_create = min(
                metrics.items(),
                key=lambda x: x[1].operations.get("create_sandbox", {}).get("avg_ms", float("inf")),
            )
            print(
                f"Fastest Creation: {fastest_create[0]} "
                f"({fastest_create[1].operations.get('create_sandbox', {}).get('avg_ms', 0):.2f}ms)"
            )

            # Fastest execution
            exec_ops = [
                op
                for op in ["execute_simple_echo", "execute_python_calculation"]
                if any(op in m.operations for m in metrics.values())
            ]

            if exec_ops:
                fastest_exec = None
                fastest_time = float("inf")
                fastest_op = None

                for provider, m in metrics.items():
                    for op in exec_ops:
                        if op in m.operations:
                            op_time = m.operations[op]["avg_ms"]
                            if op_time < fastest_time:
                                fastest_time = op_time
                                fastest_exec = provider
                                fastest_op = op

                if fastest_exec:
                    print(
                        f"Fastest Execution: {fastest_exec} ({fastest_time:.2f}ms for {fastest_op})"
                    )

            # Most reliable
            most_reliable = max(metrics.items(), key=lambda x: x[1].success_rate)
            print(
                f"Most Reliable: {most_reliable[0]} ({most_reliable[1].success_rate*100:.1f}% success)"
            )

            # Best for reuse
            reuse_candidates = {
                p: m.operations.get("find_and_reuse", {})
                for p, m in metrics.items()
                if "find_and_reuse" in m.operations
            }

            if reuse_candidates:
                best_reuse = min(
                    reuse_candidates.items(), key=lambda x: x[1].get("avg_ms", float("inf"))
                )
                print(f"Best Reuse: {best_reuse[0]} ({best_reuse[1].get('avg_ms', 0):.2f}ms)")

    async def run(self) -> Dict[str, ProviderMetrics]:
        """Run full benchmark suite."""
        print("🚀 Starting Sandbox Provider Benchmarks")
        print(f"   Iterations per test: {self.iterations}")
        print(f"   Providers: {', '.join(self.providers.keys())}")

        # Run benchmarks for each provider
        all_results = []
        for provider_name in self.providers:
            try:
                results = await self.run_provider_benchmark(provider_name)
                all_results.extend(results)
                self.results.extend(results)
            except Exception as e:
                print(f"❌ Failed to benchmark {provider_name}: {e}")

        # Calculate metrics
        metrics = self.calculate_metrics(all_results)

        # Print results
        self.print_results(metrics)

        # Save results to file
        self.save_results(all_results, metrics)

        return metrics

    def save_results(self, results: List[BenchmarkResult], metrics: Dict[str, ProviderMetrics]):
        """Save benchmark results to JSON file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"benchmark_results_{timestamp}.json"

        data = {
            "timestamp": timestamp,
            "iterations": self.iterations,
            "providers": list(self.providers.keys()),
            "results": [asdict(r) for r in results],
            "metrics": {k: asdict(v) for k, v in metrics.items()},
        }

        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

        print(f"\n💾 Results saved to {filename}")


async def main():
    """Run benchmark suite."""
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark sandbox providers")
    parser.add_argument(
        "--iterations", type=int, default=5, help="Number of iterations per test (default: 5)"
    )
    args = parser.parse_args()

    benchmark = SandboxBenchmark(iterations=args.iterations)
    await benchmark.run()


if __name__ == "__main__":
    asyncio.run(main())
