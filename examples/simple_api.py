"""Examples of the simplified high-level Sandbox API."""

import asyncio

from sandboxes import Sandbox, run, run_many


async def main():
    print("=== Simplified Sandbox API Examples ===\n")

    # 1. Simplest usage - one-shot command execution
    print("1. One-shot command execution:")
    result = await run("echo 'Hello from sandbox!'")
    print(f"Output: {result.stdout}")
    print()

    # 2. Execute multiple commands
    print("2. Execute multiple commands:")
    results = await run_many(
        [
            "echo 'First command'",
            "echo 'Second command'",
            "python -c 'print(\"Python works!\")'",
        ]
    )
    for i, result in enumerate(results, 1):
        print(f"Command {i}: {result.stdout.strip()}")
    print()

    # 3. Create sandbox with auto-detection
    print("3. Auto-detect available providers:")
    sandbox = await Sandbox.create()
    print(f"Created: {sandbox}")
    result = await sandbox.execute("pwd")
    print(f"Working directory: {result.stdout.strip()}")
    await sandbox.destroy()
    print()

    # 4. Reusable sandbox with context manager
    print("4. Reusable sandbox with context manager:")
    async with Sandbox.create(labels={"session": "demo"}) as sandbox:
        # Install a package
        await sandbox.execute("pip install --quiet requests")

        # Use the package
        result = await sandbox.execute("python -c 'import requests; print(requests.__version__)'")
        print(f"Requests version: {result.stdout.strip()}")
    print("Sandbox automatically cleaned up!")
    print()

    # 5. Streaming output
    print("5. Stream command output:")
    async with Sandbox.create() as sandbox:
        print("Streaming output: ", end="")
        async for chunk in sandbox.stream('for i in 1 2 3; do echo -n "$i... "; sleep 0.5; done'):
            print(chunk, end="", flush=True)
        print("\nDone!")
    print()

    # 6. File operations
    print("6. File upload/download:")
    async with Sandbox.create() as sandbox:
        # Create a local file
        with open("/tmp/test.txt", "w") as f:
            f.write("Hello from local system!")

        # Upload to sandbox
        await sandbox.upload("/tmp/test.txt", "/workspace/uploaded.txt")

        # Process in sandbox
        await sandbox.execute("echo ' (processed in sandbox)' >> /workspace/uploaded.txt")

        # Download back
        await sandbox.download("/workspace/uploaded.txt", "/tmp/result.txt")

        with open("/tmp/result.txt") as f:
            print(f"Downloaded content: {f.read().strip()}")
    print()

    # 7. Provider selection with fallback
    print("7. Specific provider with fallback:")
    sandbox = await Sandbox.create(
        provider="e2b",  # Try E2B first
        fallback=["modal", "cloudflare", "daytona"],  # Fallback order
        labels={"purpose": "testing"},
    )
    print(f"Using provider: {sandbox._provider_name}")
    await sandbox.destroy()
    print()

    # 8. Find or create pattern
    print("8. Get or create sandbox by labels:")
    # First call creates
    sandbox1 = await Sandbox.get_or_create(labels={"app": "myapp", "env": "dev"})
    print(f"First call - Created: {sandbox1.id}")

    # Second call finds existing
    sandbox2 = await Sandbox.get_or_create(labels={"app": "myapp", "env": "dev"})
    print(f"Second call - Reused: {sandbox2.id}")
    print(f"Same sandbox? {sandbox1.id == sandbox2.id}")

    await sandbox1.destroy()
    print()

    print("=== All examples completed! ===")


if __name__ == "__main__":
    # Auto-configure from environment variables
    # Or manually configure:
    # Sandbox.configure(
    #     e2b_api_key="your-key",
    #     default_provider="e2b"
    # )

    asyncio.run(main())
