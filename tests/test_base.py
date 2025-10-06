"""Unit tests for base abstractions."""

from datetime import datetime

import pytest

from sandboxes.base import (
    ExecutionResult,
    Sandbox,
    SandboxConfig,
    SandboxProvider,
    SandboxState,
)


@pytest.mark.unit
class TestSandboxConfig:
    """Test SandboxConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = SandboxConfig()
        assert config.image is None
        assert config.language is None
        assert config.timeout_seconds == 120
        assert config.env_vars == {}
        assert config.labels == {}
        assert config.setup_commands == []

    def test_custom_config(self):
        """Test custom configuration."""
        config = SandboxConfig(
            image="python:3.11",
            language="python",
            memory_mb=2048,
            cpu_cores=2.0,
            timeout_seconds=60,
            env_vars={"API_KEY": "test"},
            labels={"session": "123"},
            setup_commands=["pip install pandas"],
            working_dir="/app",
            provider_config={"custom": "value"},
        )

        assert config.image == "python:3.11"
        assert config.memory_mb == 2048
        assert config.cpu_cores == 2.0
        assert config.env_vars["API_KEY"] == "test"
        assert config.labels["session"] == "123"
        assert "pip install pandas" in config.setup_commands
        assert config.provider_config["custom"] == "value"


@pytest.mark.unit
class TestExecutionResult:
    """Test ExecutionResult dataclass."""

    def test_successful_execution(self):
        """Test successful execution result."""
        result = ExecutionResult(
            exit_code=0,
            stdout="Hello, World!",
            stderr="",
            duration_ms=100,
        )

        assert result.success is True
        assert result.exit_code == 0
        assert "Hello, World!" in result.stdout
        assert result.truncated is False
        assert result.timed_out is False

    def test_failed_execution(self):
        """Test failed execution result."""
        result = ExecutionResult(
            exit_code=1,
            stdout="",
            stderr="Error: Command failed",
        )

        assert result.success is False
        assert result.exit_code == 1
        assert "Error" in result.stderr

    def test_timeout_execution(self):
        """Test timed out execution."""
        result = ExecutionResult(
            exit_code=-1,
            stdout="",
            stderr="Timeout",
            timed_out=True,
        )

        assert result.success is False
        assert result.timed_out is True


@pytest.mark.unit
class TestSandbox:
    """Test Sandbox dataclass."""

    def test_sandbox_creation(self):
        """Test sandbox object creation."""
        sandbox = Sandbox(
            id="test-123",
            provider="e2b",
            state=SandboxState.RUNNING,
            labels={"test": "true"},
            created_at=datetime.now(),
            metadata={"custom": "data"},
        )

        assert sandbox.id == "test-123"
        assert sandbox.provider == "e2b"
        assert sandbox.state == SandboxState.RUNNING
        assert sandbox.labels["test"] == "true"
        assert "custom" in sandbox.metadata

    def test_sandbox_states(self):
        """Test all sandbox states."""
        states = [
            SandboxState.CREATING,
            SandboxState.STARTING,
            SandboxState.RUNNING,
            SandboxState.STOPPING,
            SandboxState.STOPPED,
            SandboxState.TERMINATED,
            SandboxState.ERROR,
        ]

        for state in states:
            sandbox = Sandbox(id=f"test-{state.value}", provider="test", state=state)
            assert sandbox.state == state


@pytest.mark.unit
class TestSandboxProvider:
    """Test SandboxProvider abstract base class."""

    @pytest.mark.asyncio
    async def test_default_find_sandbox(self):
        """Test default find_sandbox implementation."""

        # Create a mock provider
        class MockProvider(SandboxProvider):
            @property
            def name(self):
                return "mock"

            async def create_sandbox(self, config):
                pass

            async def get_sandbox(self, sandbox_id):
                pass

            async def list_sandboxes(self, labels=None):
                if labels and labels.get("test") == "true":
                    return [
                        Sandbox(id="running-1", provider="mock", state=SandboxState.RUNNING),
                        Sandbox(id="stopped-1", provider="mock", state=SandboxState.STOPPED),
                    ]
                return []

            async def execute_command(self, sandbox_id, command, timeout=None, env_vars=None):
                pass

            async def destroy_sandbox(self, sandbox_id):
                pass

        provider = MockProvider()

        # Should find running sandbox
        sandbox = await provider.find_sandbox({"test": "true"})
        assert sandbox is not None
        assert sandbox.id == "running-1"
        assert sandbox.state == SandboxState.RUNNING

        # Should return None if no matching sandboxes
        sandbox = await provider.find_sandbox({"test": "false"})
        assert sandbox is None

    @pytest.mark.asyncio
    async def test_default_get_or_create_sandbox(self):
        """Test default get_or_create_sandbox implementation."""

        class MockProvider(SandboxProvider):
            @property
            def name(self):
                return "mock"

            async def create_sandbox(self, config):
                return Sandbox(
                    id="new-sandbox",
                    provider="mock",
                    state=SandboxState.RUNNING,
                    labels=config.labels,
                )

            async def get_sandbox(self, sandbox_id):
                pass

            async def list_sandboxes(self, labels=None):
                # Return existing sandbox for specific label
                if labels and labels.get("existing") == "true":
                    return [
                        Sandbox(
                            id="existing-sandbox",
                            provider="mock",
                            state=SandboxState.RUNNING,
                            labels=labels,
                        )
                    ]
                return []

            async def execute_command(self, sandbox_id, command, timeout=None, env_vars=None):
                pass

            async def destroy_sandbox(self, sandbox_id):
                pass

        provider = MockProvider()

        # Should reuse existing sandbox
        config = SandboxConfig(labels={"existing": "true"})
        sandbox = await provider.get_or_create_sandbox(config)
        assert sandbox.id == "existing-sandbox"

        # Should create new sandbox
        config = SandboxConfig(labels={"new": "true"})
        sandbox = await provider.get_or_create_sandbox(config)
        assert sandbox.id == "new-sandbox"

    @pytest.mark.asyncio
    async def test_execute_commands(self):
        """Test executing multiple commands."""

        class MockProvider(SandboxProvider):
            @property
            def name(self):
                return "mock"

            async def create_sandbox(self, config):
                pass

            async def get_sandbox(self, sandbox_id):
                pass

            async def list_sandboxes(self, labels=None):
                return []

            async def execute_command(self, sandbox_id, command, timeout=None, env_vars=None):
                if "error" in command:
                    return ExecutionResult(exit_code=1, stdout="", stderr="Error occurred")
                return ExecutionResult(exit_code=0, stdout=f"Output: {command}", stderr="")

            async def destroy_sandbox(self, sandbox_id):
                pass

        provider = MockProvider()

        # All commands succeed
        results = await provider.execute_commands("test-sandbox", ["echo 1", "echo 2", "echo 3"])
        assert len(results) == 3
        assert all(r.success for r in results)

        # Stop on error
        results = await provider.execute_commands(
            "test-sandbox", ["echo 1", "error command", "echo 3"], stop_on_error=True
        )
        assert len(results) == 2  # Should stop after error
        assert results[0].success is True
        assert results[1].success is False

        # Continue on error
        results = await provider.execute_commands(
            "test-sandbox", ["echo 1", "error command", "echo 3"], stop_on_error=False
        )
        assert len(results) == 3  # Should continue after error
        assert results[0].success is True
        assert results[1].success is False
        assert results[2].success is True
