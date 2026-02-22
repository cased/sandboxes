# Sandboxes Benchmarks

Benchmark suite for comparing sandbox provider performance.

## Quick Start

```bash
# Run the primary benchmark
python benchmarks/ttfc_benchmark.py

# Quick 5-iteration test
python benchmarks/ttfc_benchmark.py --iterations 5 --warmup 1
```

## Methodology

The primary benchmark (`ttfc_benchmark.py`) is designed for statistically meaningful results:

- **50 iterations** by default for reliable percentile calculations
- **3 warmup runs** discarded to eliminate one-time initialization costs
- **Full percentiles**: p50, p75, p99, p99.9 to expose tail latencies

## Benchmarks

| Benchmark | Purpose | When to Use |
|-----------|---------|-------------|
| `ttfc_benchmark.py` | Time to First Command | **Start here.** Primary benchmark for provider comparison. |
| `comprehensive_benchmark.py` | Diverse workloads | Testing different workload types (CPU, I/O, packages). |
| `compare_providers.py` | Lifecycle breakdown | Understanding create/execute/destroy overhead. |
| `benchmark_20x.py` | Throughput | Testing concurrent sandbox creation. |
| `cold_vs_warm.py` | Variance analysis | Investigating startup consistency. |
| `image_reuse.py` | Caching behavior | Understanding image/template caching. |

## Primary Benchmark: Time to First Command

TTFC measures what users care about: how long until they can run code.

```
TTFC = create_sandbox() + first command execution
```

Teardown is not included since it happens after the user's work is done.

### Usage

```bash
# Full run (50 iterations + 3 warmup per provider)
python benchmarks/ttfc_benchmark.py

# Specific providers
python benchmarks/ttfc_benchmark.py --providers daytona,e2b

# More iterations for better p99.9
python benchmarks/ttfc_benchmark.py --iterations 100 --warmup 5
```

### Output

```
Provider     | p50 (s)    | p75 (s)    | p99 (s)    | p99.9 (s)  | Min (s)    | Max (s)    | Status
-------------+------------+------------+------------+------------+------------+------------+-----------
daytona      | 0.36       | 0.49       | 0.50       | 0.50       | 0.35       | 0.50       | 50/50 OK
e2b          | 0.47       | 0.51       | 0.57       | 0.57       | 0.34       | 0.57       | 50/50 OK
modal        | 2.50       | 2.80       | 3.17       | 3.18       | 2.42       | 3.18       | 50/50 OK
```

Results are saved to `ttfc_results_<timestamp>.json`.

## Configuration

Benchmarks auto-detect configured providers:

| Provider | Configuration |
|----------|--------------|
| Daytona | `DAYTONA_API_KEY` |
| E2B | `E2B_API_KEY` |
| Modal | `modal token set` or `MODAL_TOKEN_ID` |
| Hopx | `HOPX_API_KEY` |
| Sprites | `SPRITES_TOKEN` or `sprite login` |

## Methodology Notes

**Apples-to-apples comparison**: Benchmarks use comparable environments where possible:
- Modal/Daytona: `daytonaio/ai-test:0.2.3` (Python 3.13 + common packages)
- E2B/Hopx: `code-interpreter` template

**Fresh sandboxes**: Each iteration creates a new sandbox. No sandbox pooling or reuse.

**Sequential execution**: Providers are tested one at a time to avoid interference.

**Cloudflare excluded**: The Cloudflare provider has different semantics and is excluded by default.
