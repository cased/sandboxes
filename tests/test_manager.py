"""Tests for the Manager orchestration class."""

import pytest

from sandboxes import ExecutionResult, Manager, SandboxConfig
from sandboxes.base import Sandbox, SandboxProvider, SandboxState
from sandboxes.exceptions import ProviderError, SandboxError


class MockProvider(SandboxProvider):
    """Mock provider for testing."""

    def __init__(self, name: str = "mock", should_fail: bool = False):
        super().__init__()
        self._name = name
        self.should_fail = should_fail
        self.create_called = False
        self.execute_called = False

    @property
    def name(self) -> str:
        return self._name

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        self.create_called = True
        if self.should_fail:
            raise ProviderError(f"{self.name} failed")
        return Sandbox(
            id=f"{self.name}-sandbox-123",
            provider=self.name,
            state=SandboxState.RUNNING,
            labels=config.labels or {},
        )

    async def get_sandbox(self, sandbox_id: str) -> Sandbox:
        if self.should_fail:
            return None
        return Sandbox(
            id=sandbox_id,
            provider=self.name,
            state=SandboxState.RUNNING,
            labels={},
        )

    async def list_sandboxes(self, labels=None):
        return []

    async def execute_command(
        self, sandbox_id: str, command: str, timeout=None, env_vars=None
    ) -> ExecutionResult:
        self.execute_called = True
        if self.should_fail:
            raise SandboxError(f"{self.name} execution failed")
        return ExecutionResult(
            exit_code=0,
            stdout=f"Output from {self.name}: {command}",
            stderr="",
        )

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        return not self.should_fail


class TestManager:
    """Test Manager orchestration functionality."""

    @pytest.fixture
    def providers(self):
        """Create mock providers."""
        return [
            MockProvider("primary"),
            MockProvider("secondary"),
            MockProvider("tertiary"),
        ]

    @pytest.fixture
    def manager(self, providers):
        """Create manager with mock providers."""
        manager = Manager(default_provider="primary")
        # Register each provider
        manager.providers = {
            "primary": providers[0],
            "secondary": providers[1],
            "tertiary": providers[2],
        }
        return manager

    @pytest.mark.asyncio
    async def test_create_sandbox_default_provider(self, manager):
        """Test creating sandbox with default provider."""
        config = SandboxConfig(labels={"test": "default"})
        sandbox = await manager.create_sandbox(config)

        assert sandbox is not None
        assert sandbox.provider == "primary"
        assert sandbox.id == "primary-sandbox-123"
        assert manager.providers["primary"].create_called

    @pytest.mark.asyncio
    async def test_create_sandbox_specific_provider(self, manager):
        """Test creating sandbox with specific provider."""
        config = SandboxConfig(labels={"test": "specific"})
        sandbox = await manager.create_sandbox(config, provider="secondary")

        assert sandbox is not None
        assert sandbox.provider == "secondary"
        assert sandbox.id == "secondary-sandbox-123"
        assert manager.providers["secondary"].create_called
        assert not manager.providers["primary"].create_called

    @pytest.mark.asyncio
    async def test_create_sandbox_provider_fails(self, manager):
        """Test when provider fails to create sandbox."""
        # Make primary provider fail
        manager.providers["primary"].should_fail = True

        config = SandboxConfig(labels={"test": "fail"})

        with pytest.raises(ProviderError):
            await manager.create_sandbox(config, provider="primary")

    @pytest.mark.asyncio
    async def test_execute_command(self, manager):
        """Test executing command in sandbox."""
        # First create a sandbox
        config = SandboxConfig(labels={"test": "execute"})
        sandbox = await manager.create_sandbox(config)

        # Execute command
        result = await manager.execute_command(sandbox.id, "echo test", provider="primary")

        assert result.success
        assert "Output from primary: echo test" in result.stdout
        assert manager.providers["primary"].execute_called

    @pytest.mark.asyncio
    async def test_health_check(self, manager):
        """Test health check for specific provider."""

        # Add a health_check method to MockProvider
        async def mock_health_check():
            return not manager.providers["primary"].should_fail

        manager.providers["primary"].health_check = mock_health_check

        # Test health check for primary
        health = await manager.health_check(provider="primary")
        assert health["primary"] is True

        # Make primary fail and check again
        manager.providers["primary"].should_fail = True
        health = await manager.health_check(provider="primary")
        assert health["primary"] is False

    @pytest.mark.asyncio
    async def test_destroy_sandbox(self, manager):
        """Test destroying sandbox."""
        config = SandboxConfig(labels={"test": "destroy"})
        sandbox = await manager.create_sandbox(config)

        # Destroy sandbox
        destroyed = await manager.destroy_sandbox(sandbox.id, provider="primary")
        assert destroyed is True

    @pytest.mark.asyncio
    async def test_list_sandboxes(self, manager):
        """Test listing sandboxes from provider."""
        # This would need more setup for a real test
        sandboxes = await manager.list_sandboxes(provider="primary")
        assert isinstance(sandboxes, list)

    @pytest.mark.asyncio
    async def test_invalid_provider(self, manager):
        """Test using invalid provider name."""
        config = SandboxConfig(labels={"test": "invalid"})

        with pytest.raises(ProviderError) as exc_info:
            await manager.create_sandbox(config, provider="nonexistent")

        assert "Failed to create sandbox" in str(exc_info.value)
