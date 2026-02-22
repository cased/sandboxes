# Sandboxes Benchmarks

Comprehensive benchmark suite for comparing sandbox provider performance.

## Available Benchmarks

### comprehensive_benchmark.py (RECOMMENDED)
**Apples-to-apples comparison with realistic workloads**

Tests all configured providers with diverse scenarios:
- Hello World (shell execution)
- Prime Calculation (CPU-bound)
- File I/O (1000 files)
- Package Install (pip)
- NumPy FFT (numerical computation)

**Features:**
- Uses standardized runtime hints per provider:
  - Modal/Daytona: standardized Docker image
  - E2B/Hopx: configurable template IDs
  - Sprites/Vercel: provider defaults
- Multiple runs with statistical analysis (mean, stddev, min, max)
- Detailed error reporting
- Winner tracking across all tests

**Usage:**
```bash
python benchmarks/comprehensive_benchmark.py
```

**Based on:** [ai-sandbox-benchmark](https://github.com/nibzard/ai-sandbox-benchmark) (Apache 2.0)

---

### compare_providers.py
**Lifecycle breakdown (create/execute/destroy)**

Tests basic sandbox operations with detailed timing for each phase:
- Sandbox creation time
- Command execution time
- Cleanup/destroy time
- Total end-to-end time

**Best for:** Understanding overhead of each lifecycle phase

**Usage:**
```bash
python benchmarks/compare_providers.py
```

---

### tti_parity_benchmark.py
**TTI parity run with proper statistical analysis**

Measures TTI (Time to Interactive) as:
- `create_sandbox` (fresh cold-start sandbox)
- first command (`echo "benchmark"`)
- teardown not timed

**Features:**
- **50 iterations** by default (vs 10 in computesdk) for statistical significance
- **3 warmup runs** discarded to eliminate cold-start outliers
- **Percentiles**: p50, p75, p99, p99.9 (not just median/min/max)
- **Standard deviation** for variance analysis
- Sequential provider execution to avoid cross-provider interference

**Usage:**
```bash
# Default: 50 iterations + 3 warmup per provider
python benchmarks/tti_parity_benchmark.py

# Custom run
python benchmarks/tti_parity_benchmark.py \
  --providers daytona,e2b,modal \
  --iterations 100 \
  --warmup 5 \
  --create-timeout 120 \
  --command-timeout 30
```

**Output:**
```
Provider     | p50 (s)    | p75 (s)    | p99 (s)    | p99.9 (s)  | Min (s)    | Max (s)    | Status
-------------+------------+------------+------------+------------+------------+------------+-----------
daytona      | 0.59       | 0.61       | 0.63       | 0.63       | 0.55       | 0.63       | 50/50 OK
```

**Notes:**
- `ModalProvider` has a default image in this repo; optionally override with:
  `--modal-image <image>` or `BENCHMARK_PARITY_MODAL_IMAGE`.
- Results are written to `benchmarks/tti_parity_results_<timestamp>.json` unless `--output` is set.

---

### simple_benchmark.py
**Quick smoke test**

Fast create/exec/destroy verification across all configured providers.

**Usage:**
```bash
python benchmarks/simple_benchmark.py
```

---

### benchmark_20x.py
**Concurrent execution test**

Tests 20 concurrent sandbox operations to measure parallelism and throughput.

**Usage:**
```bash
python benchmarks/benchmark_20x.py
```

---

### cold_vs_warm.py
**Cold start analysis**

Compares cold start (first run) vs warm start (subsequent runs) performance.

**Usage:**
```bash
python benchmarks/cold_vs_warm.py
```

---

### image_reuse.py
**Image caching test**

Tests how providers that support explicit image/template runtime configuration
handle reuse and caching (currently Modal, Daytona, E2B, and Hopx).

**Usage:**
```bash
python benchmarks/image_reuse.py
```

---

## Configuration

All benchmarks auto-detect available providers based on environment variables:

- **Daytona**: Set `DAYTONA_API_KEY`
- **E2B**: Set `E2B_API_KEY`
- **Sprites**: Set `SPRITES_TOKEN` or run `sprite login`
- **Hopx**: Set `HOPX_API_KEY`
- **Vercel**: Set `VERCEL_TOKEN`, `VERCEL_PROJECT_ID`, and `VERCEL_TEAM_ID`
- **Modal**: Run `modal token set` or set `MODAL_TOKEN_ID`

## Standard Image

For apples-to-apples comparison, benchmarks use comparable environments:

- **Modal/Daytona**: `daytonaio/ai-test:0.2.3`
  - Python 3.13, numpy, requests, anthropic, cohere, beautifulsoup4, and many AI/ML packages
  - Both providers support arbitrary Docker images

- **E2B**: `code-interpreter` template by default
  - Python, npm, Jupyter, and common ML packages (numpy, pandas, matplotlib, etc.)
  - E2B uses templates instead of Docker images
  - Benchmarks prefer `E2B_BENCHMARK_TEMPLATE`, then `benchmarks/e2b-daytona-benchmark/e2b.toml`, then `code-interpreter`
  - Override with `E2B_BENCHMARK_TEMPLATE`
  - If you see `Template is not compatible with secured access`, set `E2B_BENCHMARK_TEMPLATE` to a secured-access compatible template ID

- **Hopx**: `code-interpreter` template by default
  - Override with `HOPX_BENCHMARK_TEMPLATE`

- **Sprites/Vercel**:
  - Benchmarks use provider defaults for runtime/image behavior

Cloudflare provider benchmarks are intentionally excluded by default.

## Contributing

When adding new benchmarks:
1. Keep provider discovery centralized (see `benchmarks/provider_matrix.py`)
2. Use standardized runtime hints for fair comparisons where possible
3. Include statistical analysis (mean, stddev)
4. Add error handling and detailed reporting
5. Update this README

## License

Comprehensive benchmark based on [ai-sandbox-benchmark](https://github.com/nibzard/ai-sandbox-benchmark) - Apache 2.0 License
