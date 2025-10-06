# sandboxes

Universal library for AI code execution sandboxes.

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

`sandboxes` provides a unified interface for sandboxed code execution across multiple providers:

- **Current providers**: E2B, Modal, Daytona
- **Experimental**: Cloudflare (requires self-hosted Worker deployment)

Write your code once and switch between providers with a single line change, or let the library automatically select the best available option.

## Installation

Add to your project:

```bash
uv add sandboxes
```

or install with your preferred Python package manager and use the CLI
for any language. 

## Quick Start

### The Simplest Way - One-Line Execution

```python
import asyncio
from sandboxes import run

async def main():
    # Creates a temporary sandbox, runs the command, then destroys the sandbox
    result = await run("echo 'Hello from sandbox!'")
    print(result.stdout)  # "Hello from sandbox!"

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
    # Create a sandbox that persists for multiple operations
    async with Sandbox.create() as sandbox:
        # Install dependencies
        await sandbox.execute("pip install numpy pandas")

        # Run your code
        result = await sandbox.execute("python analyze.py")
        print(result.stdout)

        # Upload/download files
        await sandbox.upload("data.csv", "/tmp/data.csv")
        await sandbox.download("/tmp/results.csv", "results.csv")
    # Automatically cleaned up on exit

asyncio.run(main())
```

### Streaming Output

```python
# Stream long-running commands
async with Sandbox.create() as sandbox:
    async for chunk in sandbox.stream("python train_model.py"):
        print(chunk, end="", flush=True)
```

### Smart Sandbox Reuse

```python
# First call creates a new sandbox
sandbox1 = await Sandbox.get_or_create(
    labels={"project": "ml-training", "gpu": "true"}
)

# Later calls reuse the same sandbox
sandbox2 = await Sandbox.get_or_create(
    labels={"project": "ml-training", "gpu": "true"}
)

assert sandbox1.id == sandbox2.id  # Same sandbox!
```

### Provider Selection with Automatic Failover

```python
# Control where your code runs
sandbox = await Sandbox.create(
    provider="e2b",  # Try E2B first
    fallback=["modal", "cloudflare", "daytona"],  # Automatic failover
)

# The library automatically tries the next provider if one fails
print(f"Using: {sandbox._provider_name}")

# Or specify directly with run()
result = await run("python script.py", provider="modal")  # Runs on Modal
result = await run("python script.py", provider="e2b")    # Runs on E2B
result = await run("python script.py")                     # Auto-selects
```

## Command Line Interface

`sandboxes` includes a powerful CLI for running code in **any language** from your terminal. Execute TypeScript, Go, Rust, Python, or any other language in isolated sandboxes.

### Installation

The CLI is installed automatically with the package:

```bash
uv add sandboxes
# or
pip install sandboxes
```

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

**Three unix ways:**

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

Supported languages: `python`, `node/javascript`, `typescript`, `go`, `rust`, `bash/sh`

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

**Auto-Dependency Installation:** Use `--deps` to automatically install dependencies from `go.mod` (located in the same directory as your code file). The CLI will upload `go.mod` and `go.sum` (if present) and run `go mod download` before executing your code.


## Provider Configuration

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
sandbox = await Sandbox.create()  # Auto-selects first available provider
```

#### How Auto-Detection Works

When you call `Sandbox.create()` or `run()`, the library:

1. **Checks for E2B**: Looks for `E2B_API_KEY` environment variable
2. **Checks for Modal**: Looks for `~/.modal.toml` file or `MODAL_TOKEN_ID` env var
3. **Checks for Daytona**: Looks for `DAYTONA_API_KEY` environment variable
4. **Checks for Cloudflare** *(experimental)*: Looks for both `CLOUDFLARE_SANDBOX_BASE_URL` and `CLOUDFLARE_API_TOKEN`

The first provider with valid credentials is used. Cloudflare is experimental and requires deploying your own Worker. You can see which providers are detected:

```python
from sandboxes import Sandbox

# Force detection
Sandbox._ensure_manager()

# Check what's available
if Sandbox._manager:
    print(f"Available providers: {list(Sandbox._manager.providers.keys())}")
    print(f"Default provider: {Sandbox._manager.default_provider}")
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

### Custom Images and Templates

```python
# Use custom Docker images with Modal
config = SandboxConfig(image="python:3.12-slim")
sandbox = await modal_provider.create_sandbox(config)

# Use E2B templates
config = SandboxConfig(image="your-template-id")
sandbox = await e2b_provider.create_sandbox(config)

# Use Daytona snapshots
config = SandboxConfig(image="your-snapshot-name")
sandbox = await daytona_provider.create_sandbox(config)

# Via CLI
sandboxes run "python --version" --image python:3.12-alpine
```

## Advanced Usage

### Multi-Provider Orchestration

```python
from sandboxes import Manager, SandboxConfig

# Initialize manager with multiple providers
manager = Manager(
    providers=[
        E2BProvider(),
        ModalProvider(),
        DaytonaProvider(),
        CloudflareProvider(base_url="https://your-worker.workers.dev", api_token="..."),
    ],
    default_provider="e2b"
)

# Manager handles failover automatically
sandbox = await manager.create_sandbox(
    SandboxConfig(labels={"task": "test"}),
    fallback=True  # Try other providers if primary fails
)
```

### Sandbox Reuse (Provider-Level)

For advanced control, work directly with providers instead of the high-level `Sandbox` API:

```python
# Sandboxes can be reused based on labels
config = SandboxConfig(
    labels={"project": "ml-training", "gpu": "true"}
)

# This will find existing sandbox or create new one
sandbox = await provider.get_or_create_sandbox(config)

# Later in another process...
# This will find the same sandbox
sandbox = await provider.find_sandbox({"project": "ml-training"})
```

### Streaming Execution

```python
# Stream output as it's generated
async for chunk in provider.stream_execution(
    sandbox.id,
    "for i in range(10): print(i); time.sleep(1)"
):
    print(chunk, end="")
```

### Connection Pooling

```python
from sandboxes.pool import ConnectionPool

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
```

### Running Go

```python
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
```

### Multi-Language AI Agent

```python
async def execute_code(code: str, language: str):
    """Execute code in any language."""
    async with Sandbox.create() as sandbox:
        if language == "python":
            result = await sandbox.execute(f"python3 -c '{code}'")

        elif language == "javascript":
            result = await sandbox.execute(f"node -e '{code}'")

        elif language == "typescript":
            await sandbox.execute(f"echo '{code}' > /tmp/code.ts")
            result = await sandbox.execute("npx -y ts-node /tmp/code.ts")

        elif language == "go":
            await sandbox.execute(f"echo '{code}' > /tmp/main.go")
            result = await sandbox.execute("go run /tmp/main.go")

        elif language == "rust":
            await sandbox.execute(f"echo '{code}' > /tmp/main.rs")
            await sandbox.execute("rustc /tmp/main.rs -o /tmp/app")
            result = await sandbox.execute("/tmp/app")

        else:
            raise ValueError(f"Unsupported language: {language}")

        return result.stdout if result.success else result.stderr
```

## Common Use Cases

### AI Agent Code Execution

```python
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
```

### Data Processing Pipeline

```python
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
```

### Code Testing and Validation

```python
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
```

## More Examples

### Web Scraper Sandbox

```python
async def scrape_in_sandbox(url: str):
    async with Sandbox.create() as sandbox:
        # Install dependencies
        await sandbox.execute("pip install beautifulsoup4 requests")

        # Execute scraping code
        code = f"""
import requests
from bs4 import BeautifulSoup

resp = requests.get("{url}")
soup = BeautifulSoup(resp.text, 'html.parser')
print(soup.title.text)
"""
        result = await sandbox.execute(f"python3 -c '{code}'")
        return result.stdout
```

### ML Training Sandbox

```python
async def train_model_sandboxed():
    # Use Modal for GPU support
    provider = ModalProvider()

    sandbox = await provider.create_sandbox(
        SandboxConfig(
            image="pytorch/pytorch:latest",
            labels={"task": "ml-training"},
            timeout_seconds=3600,
            provider_config={
                "gpu": "T4",
                "memory": 8192
            }
        )
    )

    # Upload and run training script
    result = await provider.execute_command(
        sandbox.id,
        "python3 train.py --epochs 10"
    )

    await provider.destroy_sandbox(sandbox.id)
    return result
```

### Multi-Provider Fallback

```python
async def reliable_execution(code: str):
    manager = Manager(
        providers=[
            E2BProvider(),      # Primary
            ModalProvider(),    # Fallback 1
            DaytonaProvider(),  # Fallback 2
        ]
    )

    # Automatically tries providers in order until success
    result = await manager.execute_with_fallback(
        code,
        SandboxConfig(labels={"reliability": "high"})
    )

    return result
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
```

### Debugging

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Check provider health
from sandboxes import Sandbox
Sandbox._ensure_manager()
for name, provider in Sandbox._manager.providers.items():
    health = await provider.health_check()
    print(f"{name}: {'✅' if health else '❌'}")
```

## License

MIT License - see [LICENSE](LICENSE) file for details.
## Acknowledgments

Built by [Cased](https://cased.com)

Special thanks to the teams at E2B, Modal, Daytona, and Cloudflare for their excellent sandbox platforms.
