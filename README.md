# sandboxes

Universal library for AI code execution sandboxes.

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

`sandboxes` provides a unified interface for sandboxed code execution across multiple providers:

- **Current providers**: E2B, Modal, Daytona
- **Experimental**: Cloudflare (requires self-hosted Worker deployment)

Write your code once and switch between providers with a single line change, or let the library automatically select a provider.
Includes a Python API plus full-featured CLI for use from any runtime.

## Installation

Add to your project:

```bash
uv add cased-sandboxes
```

or install with your preferred Python package manager and use the CLI
for any language, e.g.,:

```bash
uv pip install cased-sandboxes
```

## Quick Start

### One-line Execution + Auto-select Provider

```python
import asyncio
from sandboxes import run

async def main():
    # Creates a temporary sandbox, runs the command, then destroys the sandbox
    result = await run("echo 'Hello from sandbox!'")
    print(result.stdout)

    # Behind the scenes, run() does this:
    # 1. Auto-detects available providers (e.g., E2B, Modal, Daytona)
    # 2. Creates a new sandbox with the first available provider
    # 3. Executes your command in that isolated environment
    # 4. Returns the result
    # 5. Automatically destroys the sandbox

asyncio.run(main())
```

### Multiple Commands

```python
import asyncio
from sandboxes import run_many

async def main():
    # Execute multiple commands in one sandbox
    results = await run_many([
        "pip install requests",
        "python -c 'import requests; print(requests.__version__)'"
    ])
    for result in results:
        print(result.stdout)

asyncio.run(main())
```

### Persistent Sandbox Sessions

```python
import asyncio
from sandboxes import Sandbox

async def main():
    async with Sandbox.create() as sandbox:
        # Install dependencies
        await sandbox.execute("pip install numpy pandas")

        # Run your code
        result = await sandbox.execute("python analyze.py")
        print(result.stdout)

        await sandbox.upload("data.csv", "/tmp/data.csv")
        await sandbox.download("/tmp/results.csv", "results.csv")
    # Automatically cleaned up on exit

asyncio.run(main())
```

### Smart Sandbox Reuse

Use `get_or_create` with labels (which can include pre-set unique ids) to re-use particular sandboxes. Useful for agent sessions over time.

```python
import asyncio
from sandboxes import Sandbox

async def main():
    # First call creates a new sandbox
    sandbox1 = await Sandbox.get_or_create(
        labels={"project": "ml-training", "gpu": "true"}
    )

    # Later calls reuse the same sandbox
    sandbox2 = await Sandbox.get_or_create(
        labels={"project": "ml-training", "gpu": "true"}
    )

    assert sandbox1.id == sandbox2.id  # Same sandbox

asyncio.run(main())
```

### Provider Selection with Automatic Failover

```python
import asyncio
from sandboxes import Sandbox, run

async def main():
    # Control where your code runs
    sandbox = await Sandbox.create(
        provider="e2b",  # Try E2B first
        fallback=["modal", "cloudflare", "daytona"],  # Automatic failover
    )

    # The library automatically tries the next provider if one fails
    print(f"Using: {sandbox._provider_name}")

    # Or specify directly with run()
    result = await run("bash my-script.sh", provider="modal")

asyncio.run(main())
```

### Custom Images and Templates

```python
import asyncio
from sandboxes import Sandbox, SandboxConfig
from sandboxes.providers import ModalProvider, E2BProvider, DaytonaProvider

async def main():
    # High-level API - works with any provider
    sandbox = await Sandbox.create(image="python:3.12-slim")

    # Or with specific providers
    daytona_provider = DaytonaProvider()
    config = SandboxConfig(image="daytonaio/ai-test:0.2.3")
    sandbox = await daytona_provider.create_sandbox(config)

asyncio.run(main())

# Via CLI
# sandboxes run "python --version" --image python:3.12-slim
```

## API Reference

### Core Classes

- **`Sandbox`**: High-level interface with automatic provider management
- **`SandboxConfig`**: Configuration for sandbox creation (labels, timeout, image)
- **`ExecutionResult`**: Standardized result object (stdout, stderr, exit_code)
- **`Manager`**: Multi-provider orchestration with failover
- **`SandboxProvider`**: Abstract base class for provider implementations

### Key Methods

```python
# High-level functions
await run(command: str, provider: str = None) -> ExecutionResult
await run_many(commands: list[str], provider: str = None) -> list[ExecutionResult]

# Sandbox methods
await Sandbox.create(provider=None, fallback=None, labels=None, image=None) -> Sandbox
await Sandbox.get_or_create(labels: dict) -> Sandbox
await Sandbox.find(labels: dict) -> Sandbox | None
await sandbox.execute(command: str) -> ExecutionResult
await sandbox.execute_many(commands: list[str]) -> list[ExecutionResult]
await sandbox.stream(command: str) -> AsyncIterator[str]
await sandbox.upload(local_path: str, remote_path: str)
await sandbox.download(remote_path: str, local_path: str)
await sandbox.destroy()
```

## Command Line Interface

`sandboxes` includes a CLI for running code in **any language** from your terminal. TypeScript, Go, Rust, Python, or any other language in isolated sandboxes.
Call the CLI from any language, or write a wrapper for it.

### Quick Start

```bash
# Run TypeScript from a file
sandboxes run --file script.ts

# Run Go code from stdin
cat main.go | sandboxes run --lang go

# Direct command execution
sandboxes run "python3 -c 'print(sum(range(100)))'"

# Run with specific provider
sandboxes run "python3 --version" --provider e2b

# List all sandboxes
sandboxes list
```

### Commands

#### `run` - Execute Code

```bash
# 1. From file (auto-detects language)
sandboxes run --file script.py
sandboxes run --file main.go

# 2. From stdin/pipe
cat script.py | sandboxes run --lang python
echo 'console.log("Hello!")' | sandboxes run --lang node

# 3. Direct command
sandboxes run "python3 -c 'print(42)'"
```

**Options:**

```bash
# Specify provider
sandboxes run --file app.py -p e2b

# Environment variables
sandboxes run --file script.py -e API_KEY=secret -e DEBUG=1

# Labels for reuse
sandboxes run --file app.py -l project=myapp --reuse

# Keep sandbox (don't auto-destroy)
sandboxes run --file script.py --keep

# Timeout
sandboxes run --file script.sh -t 600
```

Supported languages with auto-detect: `python`, `node/javascript`, `typescript`, `go`, `rust`, `bash/sh`

#### `list` - List Sandboxes

View all active sandboxes:

```bash
# List all sandboxes
sandboxes list

# Filter by provider
sandboxes list -p e2b

# Filter by labels
sandboxes list -l env=prod

# JSON output
sandboxes list --json
```

#### `exec` - Execute in Existing Sandbox

```bash
sandboxes exec sb-abc123 "ls -la" -p modal
sandboxes exec sb-abc123 "python script.py" -p e2b -e DEBUG=1
```

#### `destroy` - Remove Sandbox

```bash
sandboxes destroy sb-abc123 -p e2b
```

#### `providers` - Check Providers

```bash
sandboxes providers
```

#### `test` - Test Provider Connectivity

```bash
sandboxes test          # Test all
sandboxes test -p e2b   # Test specific
```

### CLI Examples

#### Development Workflow

```bash
# Create development sandbox
sandboxes run "git clone https://github.com/user/repo.git /app" \
  -l project=myapp \
  -l env=dev \
  --keep

# List to get sandbox ID
sandboxes list -l project=myapp

# Run commands in the sandbox
sandboxes exec sb-abc123 "cd /app && npm install" -p e2b
sandboxes exec sb-abc123 "cd /app && npm test" -p e2b

# Cleanup when done
sandboxes destroy sb-abc123 -p e2b
```

#### Multi-Language Code Testing

```bash
# TypeScript
echo 'const x: number = 42; console.log(x)' > test.ts
sandboxes run --file test.ts

# Go with automatic dependency installation
sandboxes run --file main.go --deps

# Go from stdin
cat main.go | sandboxes run --lang go

# Python from remote URL
curl -s https://example.com/script.py | sandboxes run --lang python
```

**Auto-Dependency Installation (`golang` only for now):** Use `--deps` to automatically install dependencies from `go.mod` (located in the same directory as your code file). The CLI will upload `go.mod` and `go.sum` (if present) and run `go mod download` before executing your code.


## Provider Configuration

You'll need API keys from one of the supported providers.

### Automatic Configuration

The library automatically detects available providers from environment variables:

```bash
# Set any of these environment variables:
export E2B_API_KEY="..."
export MODAL_TOKEN_ID="..."  # Or use `modal token set`
export DAYTONA_API_KEY="..."
export CLOUDFLARE_SANDBOX_BASE_URL="https://your-worker.workers.dev"
export CLOUDFLARE_API_TOKEN="..."
```

Then just use:
```python
import asyncio
from sandboxes import Sandbox

async def main():
    sandbox = await Sandbox.create()  # Auto-selects first available provider

asyncio.run(main())
```

#### How Auto-Detection Works

When you call `Sandbox.create()` or `run()`, the library checks for providers in this priority order:

1. **Daytona** - Looks for `DAYTONA_API_KEY`
2. **E2B** - Looks for `E2B_API_KEY`
3. **Modal** - Looks for `~/.modal.toml` or `MODAL_TOKEN_ID`
4. **Cloudflare** *(experimental)* - Looks for `CLOUDFLARE_SANDBOX_BASE_URL` + `CLOUDFLARE_API_TOKEN`

**The first provider with valid credentials becomes the default.** Cloudflare requires deploying your own Worker.

#### Customizing the Default Provider

You can override the auto-detected default:

```python
from sandboxes import Sandbox

# Option 1: Set default provider explicitly
Sandbox.configure(default_provider="modal")

# Option 2: Specify provider per call
sandbox = await Sandbox.create(provider="e2b")

# Option 3: Use fallback chain
sandbox = await Sandbox.create(
    provider="daytona",
    fallback=["e2b", "modal"]
)

# Check which providers are available
Sandbox._ensure_manager()
print(f"Available: {list(Sandbox._manager.providers.keys())}")
print(f"Default: {Sandbox._manager.default_provider}")
```

### Manual Provider Configuration

For more control, you can configure providers manually:

```python
from sandboxes import Sandbox

# Configure providers programmatically
Sandbox.configure(
    e2b_api_key="your-key",
    cloudflare_config={
        "base_url": "https://your-worker.workers.dev",
        "api_token": "your-token",
    },
    default_provider="e2b"
)
```

### Direct Provider Usage (Low-Level API)

For advanced use cases, you can work with providers directly:

```python
from sandboxes.providers import (
    E2BProvider,
    ModalProvider,
    DaytonaProvider,
    CloudflareProvider,
)

# E2B - Uses E2B_API_KEY env var
provider = E2BProvider()

# Modal - Uses ~/.modal.toml for auth
provider = ModalProvider()

# Daytona - Uses DAYTONA_API_KEY env var
provider = DaytonaProvider()

# Cloudflare - Requires base_url and token
provider = CloudflareProvider(
    base_url="https://your-worker.workers.dev",
    api_token="your-token",
)
```

Each provider requires appropriate authentication:
- **E2B**: Set `E2B_API_KEY` environment variable
- **Modal**: Run `modal token set` to configure
- **Daytona**: Set `DAYTONA_API_KEY` environment variable
- **Cloudflare** *(experimental)*: Deploy the [Cloudflare sandbox Worker](https://github.com/cloudflare/sandbox-sdk) and set `CLOUDFLARE_SANDBOX_BASE_URL`, `CLOUDFLARE_API_TOKEN`, and (optionally) `CLOUDFLARE_ACCOUNT_ID`

> **Cloudflare setup tips (experimental)**
>
> ⚠️ **Note**: Cloudflare support is experimental and requires self-hosting a Worker.
>
> 1. Clone the Cloudflare `sandbox-sdk` repository and deploy the `examples/basic` Worker with `wrangler`.
> 2. Provision a Workers Paid plan and enable Containers + Docker Hub registry for your account.
> 3. Define a secret (e.g. `SANDBOX_API_TOKEN`) in Wrangler and reuse the same value for `CLOUDFLARE_API_TOKEN` locally.
> 4. Set `CLOUDFLARE_SANDBOX_BASE_URL` to the Worker URL (e.g. `https://cf-sandbox.your-subdomain.workers.dev`).

## Advanced Usage

### Multi-Provider Orchestration

```python
import asyncio
from sandboxes import Manager, SandboxConfig
from sandboxes.providers import E2BProvider, ModalProvider, DaytonaProvider, CloudflareProvider

async def main():
    # Initialize manager and register providers
    manager = Manager(default_provider="e2b")

    manager.register_provider("e2b", E2BProvider, {})
    manager.register_provider("modal", ModalProvider, {})
    manager.register_provider("daytona", DaytonaProvider, {})
    manager.register_provider(
        "cloudflare",
        CloudflareProvider,
        {"base_url": "https://your-worker.workers.dev", "api_token": "..."}
    )

    # Manager handles failover automatically
    sandbox = await manager.create_sandbox(
        SandboxConfig(labels={"task": "test"}),
        fallback_providers=["modal", "daytona"]  # Try these if primary fails
    )

asyncio.run(main())
```

### Sandbox Reuse (Provider-Level)

For advanced control, work directly with providers instead of the high-level `Sandbox` API:

```python
import asyncio
from sandboxes import SandboxConfig
from sandboxes.providers import E2BProvider

async def main():
    provider = E2BProvider()

    # Sandboxes can be reused based on labels
    config = SandboxConfig(
        labels={"project": "ml-training", "gpu": "true"}
    )

    # This will find existing sandbox or create new one
    sandbox = await provider.get_or_create_sandbox(config)

    # Later in another process...
    # This will find the same sandbox
    sandbox = await provider.find_sandbox({"project": "ml-training"})

asyncio.run(main())
```

### Streaming Execution

```python
import asyncio
from sandboxes.providers import E2BProvider

async def main():
    provider = E2BProvider()
    sandbox = await provider.create_sandbox()

    # Stream output as it's generated
    async for chunk in provider.stream_execution(
        sandbox.id,
        "for i in range(10): print(i); time.sleep(1)"
    ):
        print(chunk, end="")

asyncio.run(main())
```

### Connection Pooling

```python
import asyncio
from sandboxes import SandboxConfig
from sandboxes.pool import ConnectionPool
from sandboxes.providers import E2BProvider

async def main():
    # Create a connection pool for better performance
    pool = ConnectionPool(
        provider=E2BProvider(),
        max_connections=10,
        max_idle_time=300,
        ttl=3600
    )

    # Get or create connection
    conn = await pool.get_or_create(
        SandboxConfig(labels={"pool": "ml"})
    )

    # Return to pool when done
    await pool.release(conn)

asyncio.run(main())
```

## Architecture

### Core Components

- **`Sandbox`**: High-level interface with automatic provider management
- **`SandboxProvider`**: Abstract base class for all providers
- **`SandboxConfig`**: Configuration for sandbox creation
- **`ExecutionResult`**: Standardized execution results
- **`Manager`**: Multi-provider orchestration
- **`ConnectionPool`**: Connection pooling with TTL
- **`RetryPolicy`**: Configurable retry logic
- **`CircuitBreaker`**: Fault tolerance


## Environment Variables

```bash
# E2B
export E2B_API_KEY="e2b_..."

# Daytona
export DAYTONA_API_KEY="dtn_..."

# Modal (or use modal token set)
export MODAL_TOKEN_ID="..."
export MODAL_TOKEN_SECRET="..."

# Cloudflare
export CLOUDFLARE_SANDBOX_BASE_URL="https://your-worker.workers.dev"
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ACCOUNT_ID="..."  # Optional
```

## Multi-Language Support

While `sandboxes` is a Python library, it can execute code in **any language** available in the sandbox environment. The sandboxes run standard Linux containers, so you can execute TypeScript, Go, Rust, Java, or any other language.

### Running TypeScript

```python
import asyncio
from sandboxes import Sandbox

async def run_typescript():
    """Execute TypeScript code in a sandbox."""
    async with Sandbox.create() as sandbox:
        # TypeScript code
        ts_code = '''
const greeting: string = "Hello from TypeScript!";
const numbers: number[] = [1, 2, 3, 4, 5];
const sum: number = numbers.reduce((a, b) => a + b, 0);

console.log(greeting);
console.log(`Sum of numbers: ${sum}`);
console.log(`Type system ensures safety at compile time`);
'''

        # Run with ts-node (npx auto-installs)
        result = await sandbox.execute(
            f"echo '{ts_code}' > /tmp/app.ts && npx -y ts-node /tmp/app.ts"
        )

        print(result.stdout)
        # Output:
        # Hello from TypeScript!
        # Sum of numbers: 15
        # Type system ensures safety at compile time

asyncio.run(run_typescript())
```

### Running Go

```python
import asyncio
from sandboxes import Sandbox

async def run_go():
    """Execute Go code in a sandbox."""
    async with Sandbox.create() as sandbox:
        # Go code
        go_code = '''package main

import (
    "fmt"
    "math"
)

func main() {
    fmt.Println("Hello from Go!")

    // Calculate fibonacci
    n := 10
    fmt.Printf("Fibonacci(%d) = %d\\n", n, fibonacci(n))

    // Demonstrate type safety
    radius := 5.0
    area := math.Pi * radius * radius
    fmt.Printf("Circle area (r=%.1f): %.2f\\n", radius, area)
}

func fibonacci(n int) int {
    if n <= 1 {
        return n
    }
    return fibonacci(n-1) + fibonacci(n-2)
}
'''

        # Save and run Go code
        result = await sandbox.execute(f'''
cat > /tmp/main.go << 'EOF'
{go_code}
EOF
go run /tmp/main.go
''')

        print(result.stdout)
        # Output:
        # Hello from Go!
        # Fibonacci(10) = 55
        # Circle area (r=5.0): 78.54

asyncio.run(run_go())
```

## Common Use Cases

### AI Agent Code Execution

```python
import asyncio
from sandboxes import Sandbox

async def execute_agent_code(code: str, language: str = "python"):
    """Safely execute AI-generated code."""
    async with Sandbox.create() as sandbox:
        # Install any required packages first
        if "import" in code:
            # Extract and install imports (simplified)
            await sandbox.execute("pip install requests numpy")

        # Execute the code
        result = await sandbox.execute(f"{language} -c '{code}'")

        if result.exit_code != 0:
            return f"Error: {result.stderr}"
        return result.stdout

# Example usage
asyncio.run(execute_agent_code("print('Hello!')", "python"))
```

### Data Processing Pipeline

```python
import asyncio
from sandboxes import Sandbox

async def process_dataset(dataset_url: str):
    """Process data in isolated environment."""
    async with Sandbox.create(labels={"task": "data-pipeline"}) as sandbox:
        # Setup environment
        await sandbox.execute_many([
            "pip install pandas numpy scikit-learn",
            f"wget {dataset_url} -O data.csv"
        ])

        # Upload processing script
        await sandbox.upload("process.py", "/tmp/process.py")

        # Run processing with streaming output
        async for output in sandbox.stream("python /tmp/process.py"):
            print(output, end="")

        # Download results
        await sandbox.download("/tmp/results.csv", "results.csv")

# Example usage
asyncio.run(process_dataset("https://example.com/data.csv"))
```

### Code Testing and Validation

```python
import asyncio
from sandboxes import Sandbox

async def test_solution(code: str, test_cases: list):
    """Test code against multiple test cases."""
    results = []

    async with Sandbox.create() as sandbox:
        # Save the code
        await sandbox.upload("solution.py", "/tmp/solution.py")

        # Run each test case
        for i, test in enumerate(test_cases):
            result = await sandbox.execute(
                f"python /tmp/solution.py < {test['input']}"
            )
            results.append({
                "test": i + 1,
                "passed": result.stdout.strip() == test['expected'],
                "output": result.stdout.strip()
            })

    return results

# Example usage
asyncio.run(test_solution("print(sum(map(int, input().split())))", [
    {"input": "1 2 3", "expected": "6"}
]))
```

## Troubleshooting

### No Providers Available

```python
# If you see: "No provider specified and no default provider set"

# Solution 1: Set environment variables
export E2B_API_KEY="your-key"

# Solution 2: Configure manually
from sandboxes import Sandbox
Sandbox.configure(e2b_api_key="your-key")

# Solution 3: Use low-level API
from sandboxes.providers import E2BProvider
provider = E2BProvider(api_key="your-key")
```

### Provider Failures

```python
import asyncio
from sandboxes import Sandbox
from sandboxes.exceptions import ProviderError

async def main():
    # Enable automatic failover
    sandbox = await Sandbox.create(
        provider="e2b",
        fallback=["modal", "cloudflare", "daytona"]
    )

    # Or handle errors manually
    try:
        sandbox = await Sandbox.create(provider="e2b")
    except ProviderError:
        sandbox = await Sandbox.create(provider="modal")

asyncio.run(main())
```

### Debugging

```python
import asyncio
import logging
from sandboxes import Sandbox

async def main():
    # Enable debug logging
    logging.basicConfig(level=logging.DEBUG)

    # Check provider health
    Sandbox._ensure_manager()
    for name, provider in Sandbox._manager.providers.items():
        health = await provider.health_check()
        print(f"{name}: {'✅' if health else '❌'}")

asyncio.run(main())
```

## Security Disclosure

We take security seriously. If you discover a security vulnerability in this library or any of its dependencies, please report it responsibly.

**Responsible Disclosure:**
- Email security reports to: **ted@cased.com**
- Include a detailed description of the vulnerability
- Provide steps to reproduce if possible
- Allow reasonable time for a fix before public disclosure

We will acknowledge your report within 48 hours and work with you to address the issue.

## License

MIT License - see [LICENSE](LICENSE) file for details.
## Acknowledgments

Built by [Cased](https://cased.com)

Special thanks to the teams at E2B, Modal, Daytona, and Cloudflare for their excellent sandbox platforms.
