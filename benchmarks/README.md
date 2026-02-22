# Sandboxes Benchmarks

Benchmark suite for comparing sandbox provider performance.

## Benchmarks

| Benchmark | Purpose |
|-----------|---------|
| `ttfc_benchmark.py` | Startup/initialization time (Time to First Command) |
| `comprehensive_benchmark.py` | Diverse workloads (CPU, I/O, package install) |
| `compare_providers.py` | Lifecycle breakdown (create/execute/destroy) |
| `benchmark_20x.py` | Concurrent sandbox throughput |
| `cold_vs_warm.py` | Startup variance analysis |
| `image_reuse.py` | Image/template caching behavior |

## Time to First Command

TTFC measures how long until you can run code: `create_sandbox()` + first command.

```bash
python benchmarks/ttfc_benchmark.py
python benchmarks/ttfc_benchmark.py --providers daytona,e2b --iterations 100
```

- 50 iterations by default
- 3 warmup runs discarded
- Reports p50, p75, p99, p99.9 percentiles

```
Provider     | p50 (s)    | p75 (s)    | p99 (s)    | p99.9 (s)  | Min (s)    | Max (s)    | Status
-------------+------------+------------+------------+------------+------------+------------+-----------
daytona      | 0.36       | 0.49       | 0.50       | 0.50       | 0.35       | 0.50       | 50/50 OK
e2b          | 0.47       | 0.51       | 0.57       | 0.57       | 0.34       | 0.57       | 50/50 OK
modal        | 2.50       | 2.80       | 3.17       | 3.18       | 2.42       | 3.18       | 50/50 OK
```

## Configuration

Benchmarks auto-detect configured providers:

| Provider | Configuration |
|----------|--------------|
| Daytona | `DAYTONA_API_KEY` |
| E2B | `E2B_API_KEY` |
| Modal | `modal token set` or `MODAL_TOKEN_ID` |
| Hopx | `HOPX_API_KEY` |
| Sprites | `SPRITES_TOKEN` or `sprite login` |

## Notes

- Each iteration creates a fresh sandbox (no pooling)
- Providers tested sequentially to avoid interference
- Comparable environments: Modal/Daytona use `daytonaio/ai-test:0.2.3`, E2B/Hopx use `code-interpreter` template
