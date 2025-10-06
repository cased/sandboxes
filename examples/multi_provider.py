#!/usr/bin/env python
"""Example of using multiple providers with failover."""

import asyncio
import os

from sandboxes import Manager, SandboxConfig
from sandboxes.providers.daytona import DaytonaProvider
from sandboxes.providers.e2b import E2BProvider
from sandboxes.providers.modal import ModalProvider


async def main():
    """Demonstrate multi-provider failover and load balancing."""

    # Initialize providers
    providers = []

    if os.getenv("E2B_API_KEY"):
        providers.append(E2BProvider())
        print("✅ E2B provider available")

    try:
        providers.append(ModalProvider())
        print("✅ Modal provider available")
    except:
        print("❌ Modal not configured")

    if os.getenv("DAYTONA_API_KEY"):
        providers.append(DaytonaProvider())
        print("✅ Daytona provider available")

    if not providers:
        print("❌ No providers available. Please configure at least one.")
        return

    # Create manager with automatic failover
    manager = Manager(providers=providers, default_provider=providers[0].name)

    print(f"\n📦 Manager initialized with {len(providers)} providers")
    print(f"   Default: {manager.default_provider}")

    # Example 1: Simple execution with automatic failover
    print("\n1️⃣ Testing automatic failover...")
    config = SandboxConfig(labels={"example": "multi_provider", "task": "test"}, timeout_seconds=60)

    try:
        sandbox = await manager.create_sandbox(config, fallback=True)
        print(f"   Created sandbox with {sandbox.provider}: {sandbox.id}")

        result = await manager.execute_command(sandbox.id, "echo 'Hello from multiple providers!'")
        print(f"   Output: {result.stdout.strip()}")

        await manager.destroy_sandbox(sandbox.id)
        print("   ✅ Sandbox destroyed")

    except Exception as e:
        print(f"   ❌ Error: {e}")

    # Example 2: Provider-specific features
    print("\n2️⃣ Testing provider-specific features...")

    for provider in providers:
        print(f"\n   Provider: {provider.name}")

        # Create config with provider-specific image
        if provider.name == "modal":
            config = SandboxConfig(image="python:3.11-slim")
        elif provider.name == "e2b":
            config = SandboxConfig()  # Uses default template
        else:
            config = SandboxConfig(image="command-execution-env")

        try:
            sandbox = await provider.create_sandbox(config)
            result = await provider.execute_command(
                sandbox.id, "python3 -c 'import platform; print(platform.python_version())'"
            )
            print(f"   Python version: {result.stdout.strip()}")
            await provider.destroy_sandbox(sandbox.id)

        except Exception as e:
            print(f"   Error: {e}")

    # Example 3: Load balancing across providers
    print("\n3️⃣ Testing load balancing...")

    tasks = []
    for i in range(len(providers) * 2):  # Create 2 sandboxes per provider
        config = SandboxConfig(labels={"batch": "load_test", "index": str(i)})
        # Round-robin across providers
        provider = providers[i % len(providers)]
        task = create_and_execute(provider, config, i)
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful = sum(1 for r in results if not isinstance(r, Exception))
    print(f"\n   Results: {successful}/{len(tasks)} successful")

    # Example 4: Finding and reusing sandboxes
    print("\n4️⃣ Testing sandbox reuse...")

    # Create a labeled sandbox
    config = SandboxConfig(labels={"persistent": "true", "app": "ml_training"})
    sandbox = await manager.create_sandbox(config)
    print(f"   Created sandbox: {sandbox.id}")

    # Find it again
    found = await manager.find_sandbox({"app": "ml_training"})
    if found:
        print(f"   Found existing sandbox: {found.id}")
        assert found.id == sandbox.id

    # Clean up
    await manager.destroy_sandbox(sandbox.id)
    print("   ✅ Cleaned up")


async def create_and_execute(provider, config, index):
    """Helper function to create sandbox and execute command."""
    try:
        sandbox = await provider.create_sandbox(config)
        await provider.execute_command(sandbox.id, f"echo 'Task {index} on {provider.name}'")
        await provider.destroy_sandbox(sandbox.id)
        return f"Task {index}: Success on {provider.name}"
    except Exception as e:
        return f"Task {index}: Failed - {e}"


if __name__ == "__main__":
    print("🔬 Multi-Provider Example")
    print("=" * 50)
    asyncio.run(main())
