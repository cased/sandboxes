#!/usr/bin/env python3
"""TTI parity benchmark aligned with computesdk/benchmarks methodology.

This script measures Time to Interactive (TTI) as:
  create_sandbox -> first command execution

The benchmark intentionally runs providers sequentially and creates a fresh
sandbox for every iteration to reduce cross-provider interference.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.provider_matrix import PROVIDER_CONFIGURATION_HINTS, PROVIDERS
from sandboxes import SandboxConfig

DEFAULT_PROVIDERS = ("daytona", "e2b", "modal")
DEFAULT_ITERATIONS = 50
DEFAULT_WARMUP_ITERATIONS = 3
DEFAULT_CREATE_TIMEOUT_SECONDS = 120
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30
DEFAULT_DESTROY_TIMEOUT_SECONDS = 15


@dataclass
class TimingResult:
    tti_ms: float
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"ttiMs": round(self.tti_ms, 2)}
        if self.error:
            data["error"] = self.error
        return data


def _percentile(sorted_values: list[float], p: float) -> float:
    """Calculate percentile using linear interpolation (same as numpy)."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * p / 100
    f = int(k)
    c = f + 1 if f + 1 < n else f
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def _compute_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "min": 0.0,
            "max": 0.0,
            "avg": 0.0,
            "stddev": 0.0,
            "p50": 0.0,
            "p75": 0.0,
            "p99": 0.0,
            "p999": 0.0,
        }

    sorted_values = sorted(values)
    avg = statistics.mean(sorted_values)
    stddev = statistics.stdev(sorted_values) if len(sorted_values) > 1 else 0.0

    return {
        "min": sorted_values[0],
        "max": sorted_values[-1],
        "avg": avg,
        "stddev": stddev,
        "p50": _percentile(sorted_values, 50),
        "p75": _percentile(sorted_values, 75),
        "p99": _percentile(sorted_values, 99),
        "p999": _percentile(sorted_values, 99.9),
    }


def _provider_registry() -> dict[str, Any]:
    return {provider.name: provider for provider in PROVIDERS}


async def _run_iteration(
    provider: Any,
    provider_name: str,
    iteration: int,
    create_timeout: int,
    command_timeout: int,
    modal_image: str | None,
) -> TimingResult:
    sandbox_id: str | None = None
    start = time.perf_counter()

    try:
        config = SandboxConfig(
            labels={
                "benchmark": "tti_parity",
                "provider": provider_name,
                "iteration": str(iteration + 1),
            },
            timeout_seconds=create_timeout,
        )

        # Optional override for Modal since ModalProvider has a default image.
        if provider_name == "modal" and modal_image:
            config.image = modal_image

        sandbox = await provider.create_sandbox(config)
        sandbox_id = sandbox.id

        await provider.execute_command(
            sandbox_id,
            'echo "benchmark"',
            timeout=command_timeout,
        )

        return TimingResult(tti_ms=(time.perf_counter() - start) * 1000)
    except Exception as exc:
        return TimingResult(tti_ms=0.0, error=str(exc))
    finally:
        if sandbox_id:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    provider.destroy_sandbox(sandbox_id),
                    timeout=DEFAULT_DESTROY_TIMEOUT_SECONDS,
                )


async def _run_provider(
    provider_name: str,
    provider: Any,
    iterations: int,
    warmup_iterations: int,
    create_timeout: int,
    command_timeout: int,
    modal_image: str | None,
) -> dict[str, Any]:
    total_runs = warmup_iterations + iterations
    print(f"\n--- Benchmarking: {provider_name} ({warmup_iterations} warmup + {iterations} measured) ---")
    results: list[TimingResult] = []

    for i in range(total_runs):
        is_warmup = i < warmup_iterations
        run_label = f"Warmup {i + 1}/{warmup_iterations}" if is_warmup else f"Iteration {i - warmup_iterations + 1}/{iterations}"
        print(f"  {run_label}...")
        result = await _run_iteration(
            provider=provider,
            provider_name=provider_name,
            iteration=i,
            create_timeout=create_timeout,
            command_timeout=command_timeout,
            modal_image=modal_image,
        )

        # Only record non-warmup results
        if not is_warmup:
            results.append(result)

        if result.error:
            print(f"    FAILED: {result.error}")
        else:
            suffix = " (warmup, discarded)" if is_warmup else ""
            print(f"    TTI: {(result.tti_ms / 1000):.2f}s{suffix}")

    successful = [r.tti_ms for r in results if not r.error]
    payload: dict[str, Any] = {
        "provider": provider_name,
        "iterations": [r.to_json() for r in results],
        "summary": {"ttiMs": {k: round(v, 2) for k, v in _compute_stats(successful).items()}},
    }

    if not successful:
        payload["skipped"] = True
        payload["skipReason"] = "All iterations failed"

    return payload


def _print_results_table(results: list[dict[str, Any]], iterations: int, warmup: int) -> None:
    name_width = 12
    col_width = 10
    table_width = name_width + (col_width * 7) + 40

    header = [
        "Provider".ljust(name_width),
        "p50 (s)".ljust(col_width),
        "p75 (s)".ljust(col_width),
        "p99 (s)".ljust(col_width),
        "p99.9 (s)".ljust(col_width),
        "Min (s)".ljust(col_width),
        "Max (s)".ljust(col_width),
        "Status".ljust(10),
    ]
    separator = [
        "-" * name_width,
        "-" * col_width,
        "-" * col_width,
        "-" * col_width,
        "-" * col_width,
        "-" * col_width,
        "-" * col_width,
        "-" * 10,
    ]

    print("\n" + "=" * table_width)
    print("  TTI PARITY BENCHMARK RESULTS")
    print(f"  {iterations} iterations per provider, {warmup} warmup (discarded)")
    print("=" * table_width)
    print(" | ".join(header))
    print("-+-".join(separator))

    sorted_results = sorted(
        results,
        key=lambda r: (bool(r.get("skipped")), r["summary"]["ttiMs"]["p50"]),
    )

    for result in sorted_results:
        if result.get("skipped"):
            row = [
                result["provider"].ljust(name_width),
                "--".ljust(col_width),
                "--".ljust(col_width),
                "--".ljust(col_width),
                "--".ljust(col_width),
                "--".ljust(col_width),
                "--".ljust(col_width),
                "SKIPPED".ljust(10),
            ]
            print(" | ".join(row))
            continue

        summary = result["summary"]["ttiMs"]
        successful = sum(1 for item in result["iterations"] if "error" not in item)
        total = len(result["iterations"])
        row = [
            result["provider"].ljust(name_width),
            f"{summary['p50'] / 1000:.2f}".ljust(col_width),
            f"{summary['p75'] / 1000:.2f}".ljust(col_width),
            f"{summary['p99'] / 1000:.2f}".ljust(col_width),
            f"{summary['p999'] / 1000:.2f}".ljust(col_width),
            f"{summary['min'] / 1000:.2f}".ljust(col_width),
            f"{summary['max'] / 1000:.2f}".ljust(col_width),
            f"{successful}/{total} OK".ljust(10),
        ]
        print(" | ".join(row))

    print("\nTTI = Time to Interactive (create_sandbox + first command execution)")
    print("Each iteration uses a fresh cold-start sandbox.\n")


def _provider_setup_issues(selected_providers: list[str]) -> list[dict[str, Any]]:
    registry = _provider_registry()
    issues: list[dict[str, Any]] = []

    for name in selected_providers:
        provider_spec = registry.get(name)
        if not provider_spec:
            issues.append(
                {
                    "provider": name,
                    "iterations": [],
                    "summary": {"ttiMs": _compute_stats([])},
                    "skipped": True,
                    "skipReason": f"Unknown provider: {name}",
                }
            )
            continue

        if not provider_spec.is_configured():
            hint = PROVIDER_CONFIGURATION_HINTS.get(name, "missing credentials/configuration")
            issues.append(
                {
                    "provider": name,
                    "iterations": [],
                    "summary": {"ttiMs": _compute_stats([])},
                    "skipped": True,
                    "skipReason": f"Not configured ({hint})",
                }
            )

    return issues


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run TTI parity benchmark")
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDERS),
        help=f"Comma-separated providers (default: {','.join(DEFAULT_PROVIDERS)})",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Measured iterations per provider (default: {DEFAULT_ITERATIONS})",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_ITERATIONS,
        help=f"Warmup iterations to discard (default: {DEFAULT_WARMUP_ITERATIONS})",
    )
    parser.add_argument(
        "--create-timeout",
        type=int,
        default=DEFAULT_CREATE_TIMEOUT_SECONDS,
        help=f"Create timeout seconds (default: {DEFAULT_CREATE_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--command-timeout",
        type=int,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
        help=f"First command timeout seconds (default: {DEFAULT_COMMAND_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--modal-image",
        default=os.getenv("BENCHMARK_PARITY_MODAL_IMAGE"),
        help=(
            "Optional Modal image override. Useful because ModalProvider defaults to "
            "daytonaio/ai-test:0.2.3."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to benchmarks/tti_parity_results_<timestamp>.json",
    )
    args = parser.parse_args()

    selected_providers = [name.strip() for name in args.providers.split(",") if name.strip()]
    registry = _provider_registry()

    print("TTI Parity Benchmark")
    print(f"Date: {datetime.now(UTC).isoformat()}")
    print(f"Providers: {', '.join(selected_providers)}")
    print(f"Iterations per provider: {args.iterations} (+ {args.warmup} warmup)")
    print(
        f"Timeouts: create={args.create_timeout}s, first_command={args.command_timeout}s, "
        f"destroy={DEFAULT_DESTROY_TIMEOUT_SECONDS}s"
    )
    if args.modal_image:
        print(f"Modal image override: {args.modal_image}")
    print()

    results: list[dict[str, Any]] = []
    results.extend(_provider_setup_issues(selected_providers))

    for provider_name in selected_providers:
        provider_spec = registry.get(provider_name)
        if not provider_spec or not provider_spec.is_configured():
            continue

        try:
            if provider_name == "modal" and args.modal_image:
                provider = provider_spec.load_class()(image=args.modal_image)
            else:
                provider = provider_spec.load_class()()
        except Exception as exc:
            results.append(
                {
                    "provider": provider_name,
                    "iterations": [],
                    "summary": {"ttiMs": _compute_stats([])},
                    "skipped": True,
                    "skipReason": f"Initialization failed: {exc}",
                }
            )
            continue

        provider_result = await _run_provider(
            provider_name=provider_name,
            provider=provider,
            iterations=args.iterations,
            warmup_iterations=args.warmup,
            create_timeout=args.create_timeout,
            command_timeout=args.command_timeout,
            modal_image=args.modal_image,
        )
        results.append(provider_result)

    _print_results_table(results, args.iterations, args.warmup)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_path = (
        Path(args.output)
        if args.output
        else Path(__file__).parent / f"tti_parity_results_{timestamp}.json"
    )
    payload = {
        "version": "1.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "config": {
            "providers": selected_providers,
            "iterations": args.iterations,
            "warmupIterations": args.warmup,
            "createTimeoutSeconds": args.create_timeout,
            "commandTimeoutSeconds": args.command_timeout,
            "destroyTimeoutSeconds": DEFAULT_DESTROY_TIMEOUT_SECONDS,
            "modalImageOverride": args.modal_image,
        },
        "results": results,
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Results written to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
