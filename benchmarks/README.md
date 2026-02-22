# Sandboxes Benchmarks

Benchmark suite for comparing sandbox provider performance.

## Available Benchmarks

### tti_parity_benchmark.py (PRIMARY)
**TTI measurement with proper statistical analysis**

Measures Time to Interactive (TTI): `create_sandbox` + first command execution.

- **50 iterations** by default for statistical significance
- **3 warmup runs** discarded to eliminate outliers
- **Percentiles**: p50, p75, p99, p99.9
- Fresh cold-start sandbox each iteration

```bash
python benchmarks/tti_parity_benchmark.py
python benchmarks/tti_parity_benchmark.py --providers daytona,e2b,modal --iterations 100
```

---

### comprehensive_benchmark.py
**Diverse workload comparison**

Tests multiple scenarios: Hello World, Prime Calculation, File I/O (1000 files), pip install, NumPy FFT.

```bash
python benchmarks/comprehensive_benchmark.py
```

---

### compare_providers.py
**Lifecycle breakdown**

Detailed timing for each phase: create, execute, destroy.

```bash
python benchmarks/compare_providers.py
```

---

### benchmark_20x.py
**Throughput test**

20 concurrent sandbox operations to measure parallelism.

```bash
python benchmarks/benchmark_20x.py
```

---

### cold_vs_warm.py
**Startup variance analysis**

Compares first run vs subsequent runs performance.

```bash
python benchmarks/cold_vs_warm.py
```

---

### image_reuse.py
**Image caching behavior**

Tests how providers cache images/templates across sandbox creations.

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
