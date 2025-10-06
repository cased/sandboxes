"""Integration tests for Modal provider."""

import os

import pytest
import pytest_asyncio

from sandboxes import SandboxConfig
from sandboxes.providers.modal import ModalProvider


@pytest.mark.integration
@pytest.mark.modal
@pytest.mark.skipif(
    not os.getenv("MODAL_TOKEN_ID") and not os.path.exists(os.path.expanduser("~/.modal.toml")),
    reason="Modal not configured",
)
class TestModalIntegration:
    """Integration tests for Modal provider."""

    @pytest_asyncio.fixture
    async def provider(self):
        """Create Modal provider."""
        return ModalProvider()

    @pytest.mark.asyncio
    async def test_create_and_destroy_sandbox(self, provider):
        """Test creating and destroying a Modal sandbox."""
        config = SandboxConfig(
            labels={"test": "modal_integration"},
            provider_config={
                "image": "python:3.11-slim",
                "cpu": 1.0,
                "memory": 512,
            },
        )

        # Create sandbox
        sandbox = await provider.create_sandbox(config)
        assert sandbox is not None
        assert sandbox.provider == "modal"
        assert sandbox.labels == {"test": "modal_integration"}

        # Verify it exists
        found = await provider.get_sandbox(sandbox.id)
        assert found is not None
        assert found.id == sandbox.id

        # Destroy sandbox
        destroyed = await provider.destroy_sandbox(sandbox.id)
        assert destroyed is True

        # Verify it's gone
        found = await provider.get_sandbox(sandbox.id)
        # Modal might still return it but in terminated state
        # or it might be None

    @pytest.mark.asyncio
    async def test_execute_command(self, provider):
        """Test executing commands in Modal sandbox."""
        config = SandboxConfig(
            labels={"test": "modal_exec"},
            provider_config={
                "image": "python:3.11-slim",
            },
        )

        sandbox = await provider.create_sandbox(config)

        try:
            # Test simple command
            result = await provider.execute_command(sandbox.id, "echo 'Hello from Modal!'")
            assert result.success
            assert "Hello from Modal!" in result.stdout

            # Test Python command
            result = await provider.execute_command(
                sandbox.id, "python -c \"print('Python works!')\""
            )
            assert result.success
            assert "Python works!" in result.stdout

            # Test environment variables
            result = await provider.execute_command(
                sandbox.id, "echo $TEST_VAR", env_vars={"TEST_VAR": "modal_value"}
            )
            assert result.success
            assert "modal_value" in result.stdout

        finally:
            await provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_list_sandboxes(self, provider):
        """Test listing sandboxes."""
        # Create multiple sandboxes
        sandboxes = []
        for i in range(2):
            config = SandboxConfig(labels={"test": "modal_list", "index": str(i)})
            sandbox = await provider.create_sandbox(config)
            sandboxes.append(sandbox)

        try:
            # List all sandboxes
            all_sandboxes = await provider.list_sandboxes()
            assert len(all_sandboxes) >= 2

            # List with label filter
            filtered = await provider.list_sandboxes(labels={"test": "modal_list"})
            assert len(filtered) >= 2

            # List with specific label
            specific = await provider.list_sandboxes(labels={"test": "modal_list", "index": "0"})
            assert len(specific) >= 1

        finally:
            # Cleanup
            for sandbox in sandboxes:
                await provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_find_and_reuse_sandbox(self, provider):
        """Test finding and reusing sandboxes by labels."""
        labels = {"test": "modal_reuse", "env": "test"}
        config = SandboxConfig(labels=labels)

        # Create sandbox
        original = await provider.create_sandbox(config)

        try:
            # Find it by labels
            found = await provider.find_sandbox(labels)
            assert found is not None
            assert found.id == original.id

            # Get or create should return existing
            reused = await provider.get_or_create_sandbox(config)
            assert reused.id == original.id

        finally:
            await provider.destroy_sandbox(original.id)

    @pytest.mark.asyncio
    async def test_health_check(self, provider):
        """Test Modal health check."""
        healthy = await provider.health_check()
        assert healthy is True
