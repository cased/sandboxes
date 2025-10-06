"""Pytest configuration and fixtures."""

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from sandboxes import SandboxConfig, SandboxManager
from sandboxes.providers.daytona import DaytonaProvider
from sandboxes.providers.e2b import E2BProvider


@pytest.fixture(scope="function")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def sandbox_config():
    """Basic sandbox configuration for testing."""
    return SandboxConfig(
        labels={"test": "true", "session": "pytest"},
        timeout_seconds=30,
    )


@pytest_asyncio.fixture
async def sandbox_manager() -> AsyncGenerator[SandboxManager, None]:
    """Create a sandbox manager for testing."""
    manager = SandboxManager()
    yield manager
    # Cleanup: destroy all sandboxes
    sandboxes = await manager.list_sandboxes()
    for sandbox in sandboxes:
        with contextlib.suppress(Exception):
            await manager.destroy_sandbox(sandbox.id, provider=sandbox.provider)


@pytest.fixture
def e2b_api_key():
    """Get E2B API key from environment."""
    key = os.getenv("E2B_API_KEY")
    if not key:
        pytest.skip("E2B_API_KEY not set")
    return key


@pytest.fixture
def daytona_api_key():
    """Get Daytona API key from environment."""
    key = os.getenv("DAYTONA_API_KEY")
    if not key:
        pytest.skip("DAYTONA_API_KEY not set")
    return key


@pytest.fixture
def modal_token():
    """Get Modal token from environment."""
    token = os.getenv("MODAL_TOKEN")
    if not token:
        pytest.skip("MODAL_TOKEN not set")
    return token


@pytest_asyncio.fixture
async def e2b_provider(e2b_api_key) -> AsyncGenerator[E2BProvider, None]:
    """Create E2B provider for testing."""
    provider = E2BProvider(api_key=e2b_api_key)
    yield provider
    # Cleanup
    for sandbox_id in list(provider._sandboxes.keys()):
        with contextlib.suppress(Exception):
            await provider.destroy_sandbox(sandbox_id)


@pytest_asyncio.fixture
async def daytona_provider(daytona_api_key) -> AsyncGenerator[DaytonaProvider, None]:
    """Create Daytona provider for testing."""
    provider = DaytonaProvider(api_key=daytona_api_key)
    yield provider
    # Cleanup
    sandboxes = await provider.list_sandboxes()
    for sandbox in sandboxes:
        with contextlib.suppress(Exception):
            await provider.destroy_sandbox(sandbox.id)


@pytest.fixture
def integration_test_commands():
    """Commands to test in integration tests."""
    return {
        "python": [
            "print('Hello, World!')",
            "import sys; print(sys.version)",
            "x = 5; y = 10; print(f'Sum: {x + y}')",
        ],
        "shell": [
            "echo 'Hello from shell'",
            "pwd",
            "ls -la",
        ],
        "error": [
            "raise ValueError('Test error')",
            "import nonexistent_module",
        ],
    }


@pytest.fixture
def performance_config():
    """Configuration for performance tests."""
    return {
        "num_sandboxes": 3,
        "num_commands": 10,
        "command": "print('performance test')",
        "timeout": 5,
    }


# Markers for different test types
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests that don't require external services")
    config.addinivalue_line(
        "markers", "integration: Integration tests that require real provider APIs"
    )
    config.addinivalue_line("markers", "e2b: Tests specific to E2B provider")
    config.addinivalue_line("markers", "daytona: Tests specific to Daytona provider")
    config.addinivalue_line("markers", "modal: Tests specific to Modal provider")
    config.addinivalue_line("markers", "slow: Slow tests that might take a while")
    config.addinivalue_line("markers", "cloudflare: Tests specific to Cloudflare provider")
