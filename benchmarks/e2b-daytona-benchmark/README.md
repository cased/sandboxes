# E2B Template for Daytona AI Test Image

This directory contains an E2B template built from the Daytona AI test Docker image to ensure consistent benchmarking environments.

## Building the Template

To build this template in your own E2B account:

```bash
cd benchmarks/e2b-daytona-benchmark
e2b template build
```

This will:
1. Build a Docker image from `daytonaio/ai-test:0.2.3`
2. Push it to E2B's registry
3. Create a template in your E2B account
4. Generate a unique template ID in `e2b.toml`

## Using the Template

After building, the template ID will be saved in `e2b.toml`. Use this ID in your code:

```python
from e2b import AsyncSandbox

# The template_id will be in e2b.toml after building
sandbox = await AsyncSandbox.create("YOUR_TEMPLATE_ID")
```

## For Benchmarks

The `benchmarks/benchmark.py` file is configured to use template ID `5x6hvr4zwye07thwhpkd`.

**If you build your own template**, update the template ID in `benchmarks/benchmark.py`:

```python
if provider_name == "e2b":
    # Replace with your template ID from e2b.toml
    image = "YOUR_TEMPLATE_ID"
```

## Requirements

- E2B CLI: `npm install -g @e2b/cli`
- E2B API key: Set `E2B_API_KEY` environment variable
- Docker: For building the template locally

## Why This Template?

Using the same base image (`daytonaio/ai-test:0.2.3`) for both E2B and Daytona providers ensures:
- Apples-to-apples performance comparison
- Same Python version, packages, and system configuration
- Consistent benchmark results across providers
