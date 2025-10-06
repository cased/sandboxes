# Sandboxes Benchmarks

Comprehensive benchmark suite for comparing sandbox provider performance.

## Available Benchmarks

### 🎯 comprehensive_benchmark.py (RECOMMENDED)
**Apples-to-apples comparison with realistic workloads**

Tests all providers with diverse scenarios:
- Hello World (shell execution)
- Prime Calculation (CPU-bound)
- File I/O (1000 files)
- Package Install (pip)
- NumPy FFT (numerical computation)

**Features:**
- Uses standardized image (`daytonaio/ai-test:0.2.3`) for Modal and Daytona
- Multiple runs with statistical analysis (mean, stddev, min, max)
- Detailed error reporting
- Winner tracking across all tests

**Usage:**
```bash
python benchmarks/comprehensive_benchmark.py
```

**Based on:** [ai-sandbox-benchmark](https://github.com/nibzard/ai-sandbox-benchmark) (Apache 2.0)

---

### 📊 compare_providers.py
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

### ⚡ simple_benchmark.py
**Quick smoke test**

Fast basic test to verify providers are working.

**Usage:**
```bash
python benchmarks/simple_benchmark.py
```

---

### 🔥 benchmark_20x.py
**Concurrent execution test**

Tests 20 concurrent sandbox operations to measure parallelism and throughput.

**Usage:**
```bash
python benchmarks/benchmark_20x.py
```

---

### ❄️ cold_vs_warm.py
**Cold start analysis**

Compares cold start (first run) vs warm start (subsequent runs) performance.

**Usage:**
```bash
python benchmarks/cold_vs_warm.py
```

---

### 🖼️ image_reuse.py
**Image caching test**

Tests how providers handle image reuse and caching.

**Usage:**
```bash
python benchmarks/image_reuse.py
```

---

## Configuration

All benchmarks auto-detect available providers based on environment variables:

- **E2B**: Set `E2B_API_KEY`
- **Modal**: Run `modal token set` or set `MODAL_TOKEN_ID`
- **Daytona**: Set `DAYTONA_API_KEY`

## Standard Image

For fair comparison, Modal and Daytona use the same base image:
- **Image**: `daytonaio/ai-test:0.2.3`
- **Contents**: Python 3.13, numpy, requests, anthropic, cohere, beautifulsoup4, and many AI/ML packages
- **E2B**: Uses the `base` template (standard Linux environment with Python)
  - Note: E2B supports custom templates via `config.image` or `config.provider_config["template"]`
  - All providers execute shell commands uniformly

## Interpreting Results

### Speed Rankings (Typical)
1. **Daytona** - Fastest on most workloads (~1000ms avg)
2. **E2B** - Very fast and consistent (~1200ms avg)
3. **Modal** - Slowest but reliable (~2500ms avg)

### When to Use Each Provider

**Daytona:**
- ✅ Best overall performance
- ✅ Fastest package installation
- ✅ Supports custom Docker images
- ✅ Good for AI/ML workloads

**E2B:**
- ✅ Most consistent (low variance)
- ✅ Fast and reliable
- ✅ Supports custom templates
- ✅ Good for production workloads

**Modal:**
- ✅ 100% reliable
- ✅ Excellent for custom images
- ✅ Good for long-running tasks
- ❌ Slower startup time

## Contributing

When adding new benchmarks:
1. Use the standardized image for Modal/Daytona
2. Include statistical analysis (mean, stddev)
3. Add error handling and detailed reporting
4. Update this README

## License

Comprehensive benchmark based on [ai-sandbox-benchmark](https://github.com/nibzard/ai-sandbox-benchmark) - Apache 2.0 License
