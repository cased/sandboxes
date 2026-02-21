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
**TTI parity run (closest to computesdk-style methodology)**

Measures TTI as:
- `create_sandbox` (fresh sandbox)
- first command (`echo "benchmark"`)
- teardown not timed

Runs providers sequentially and outputs JSON shaped for straightforward cross-referencing.

**Usage:**
```bash
# Default providers: daytona,e2b,modal (10 iterations each)
python benchmarks/tti_parity_benchmark.py

# Custom run
python benchmarks/tti_parity_benchmark.py \
  --providers daytona,e2b,modal \
  --iterations 10 \
  --create-timeout 120 \
  --command-timeout 30
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
