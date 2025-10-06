"""Basic usage examples for sandboxes library."""

import asyncio
import os

from sandboxes import SandboxConfig, SandboxManager


async def basic_example():
    """Basic example with E2B provider."""
    print("=== Basic E2B Example ===")

    # Initialize manager
    manager = SandboxManager(default_provider="e2b")

    # Register E2B provider
    from sandboxes.providers.e2b import E2BProvider

    manager.register_provider("e2b", E2BProvider, {"api_key": os.getenv("E2B_API_KEY")})

    # Create sandbox
    config = SandboxConfig(image="python:3.11", setup_commands=["pip install requests"])

    sandbox = await manager.create_sandbox(config)
    print(f"Created sandbox: {sandbox.id}")

    # Execute command
    result = await manager.execute_command(
        sandbox.id, "python -c 'import requests; print(requests.__version__)'"
    )
    print(f"Requests version: {result.stdout}")

    # Cleanup
    await manager.destroy_sandbox(sandbox.id)
    print("Sandbox destroyed")


async def smart_reuse_example():
    """Example showing smart sandbox reuse with Daytona."""
    print("\n=== Smart Reuse Example ===")

    manager = SandboxManager()

    # Register Daytona provider
    from sandboxes.providers.daytona import DaytonaProvider

    manager.register_provider("daytona", DaytonaProvider, {"api_key": os.getenv("DAYTONA_API_KEY")})

    # Create sandbox with labels
    config = SandboxConfig(
        labels={"session": "data-analysis-123", "user": "alice", "project": "ml-training"},
        setup_commands=["pip install pandas numpy scikit-learn"],
    )

    # First call creates new sandbox
    sandbox1 = await manager.get_or_create_sandbox(config, provider="daytona")
    print(f"First call - Sandbox ID: {sandbox1.id}")

    # Second call reuses existing sandbox (same labels)
    sandbox2 = await manager.get_or_create_sandbox(config, provider="daytona")
    print(f"Second call - Sandbox ID: {sandbox2.id}")
    print(f"Reused same sandbox: {sandbox1.id == sandbox2.id}")


async def multi_provider_example():
    """Example with multiple providers and fallback."""
    print("\n=== Multi-Provider Example ===")

    manager = SandboxManager()

    # Register multiple providers
    from sandboxes.providers.daytona import DaytonaProvider
    from sandboxes.providers.e2b import E2BProvider
    from sandboxes.providers.modal import ModalProvider

    manager.register_provider("e2b", E2BProvider, {"api_key": os.getenv("E2B_API_KEY")})

    manager.register_provider("daytona", DaytonaProvider, {"api_key": os.getenv("DAYTONA_API_KEY")})

    manager.register_provider("modal", ModalProvider, {"token": os.getenv("MODAL_TOKEN")})

    # Check health of all providers
    health = await manager.health_check()
    print(f"Provider health: {health}")

    # Create sandbox with fallback
    config = SandboxConfig(image="python:3.11", timeout_seconds=120)

    sandbox = await manager.create_sandbox(
        config,
        provider="e2b",  # Try E2B first
        fallback_providers=["daytona", "modal"],  # Fallback options
    )
    print(f"Sandbox created with provider: {sandbox.provider}")


async def batch_execution_example():
    """Example of executing multiple commands."""
    print("\n=== Batch Execution Example ===")

    from sandboxes.providers.e2b import E2BProvider

    provider = E2BProvider(api_key=os.getenv("E2B_API_KEY"))

    config = SandboxConfig()
    sandbox = await provider.create_sandbox(config)

    # Execute multiple commands
    commands = [
        "echo 'Starting setup'",
        "pip install -q pandas",
        "python -c 'import pandas; print(f\"Pandas {pandas.__version__} installed\")'",
        "echo 'Setup complete'",
    ]

    results = await provider.execute_commands(sandbox.id, commands, stop_on_error=True)

    for i, result in enumerate(results):
        print(f"Command {i + 1}: exit_code={result.exit_code}")
        if result.stdout:
            print(f"  Output: {result.stdout.strip()}")

    await provider.destroy_sandbox(sandbox.id)


async def secret_masking_example():
    """Example showing automatic secret masking."""
    print("\n=== Secret Masking Example ===")

    manager = SandboxManager()

    from sandboxes.providers.e2b import E2BProvider

    manager.register_provider("e2b", E2BProvider, {"api_key": os.getenv("E2B_API_KEY")})

    config = SandboxConfig()
    sandbox = await manager.create_sandbox(config)

    # Execute with sensitive environment variables
    result = await manager.execute_command(
        sandbox.id,
        "echo $API_KEY $DB_PASSWORD",
        env_vars={"API_KEY": "sk-secret-api-key-12345", "DB_PASSWORD": "super-secret-password"},
        mask_secrets=True,  # Secrets will be masked in output
    )

    print(f"Output (secrets masked): {result.stdout}")
    # Output will show: sk-****-45 su************rd

    await manager.destroy_sandbox(sandbox.id)


async def main():
    """Run all examples."""
    try:
        await basic_example()
        await smart_reuse_example()
        await multi_provider_example()
        await batch_execution_example()
        await secret_masking_example()
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
