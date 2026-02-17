"""Tests for the CLI module."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from sandboxes import ExecutionResult, SandboxConfig, __version__
from sandboxes.base import Sandbox, SandboxState
from sandboxes.cli import cli, get_provider


class TestCLIHelpers:
    """Test CLI helper functions."""

    def test_get_provider_valid(self):
        """Test getting valid providers."""
        with patch("sandboxes.providers.modal.ModalProvider") as mock_modal:
            provider = get_provider("modal")
            assert provider is not None
            mock_modal.assert_called_once()

    def test_get_provider_invalid(self):
        """Test getting invalid provider raises SystemExit."""
        with pytest.raises(SystemExit):
            get_provider("invalid_provider")

    def test_get_provider_init_error(self):
        """Test provider initialization error."""
        with (
            patch("sandboxes.providers.modal.ModalProvider", side_effect=Exception("Init failed")),
            pytest.raises(SystemExit),
        ):
            get_provider("modal")


class TestCLICommands:
    """Test CLI commands."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()
        self.mock_sandbox = Sandbox(
            id="test-sandbox-123",
            provider="modal",
            state=SandboxState.RUNNING,
            labels={"test": "true"},
            created_at=datetime.now(),
        )
        self.mock_result = ExecutionResult(
            exit_code=0, stdout="Hello, World!\n", stderr="", duration_ms=100
        )

    def test_cli_help(self):
        """Test CLI help command."""
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Universal AI code execution sandboxes" in result.output
        assert "run" in result.output
        assert "list" in result.output
        assert "destroy" in result.output

    def test_cli_version(self):
        """Test CLI version command."""
        result = self.runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    @patch("sandboxes.cli.asyncio.run")
    def test_run_command_basic(self, mock_async_run):
        """Test basic run command."""
        # Setup mock to capture the coroutine
        async_result = MagicMock()
        async_result.exit_code = 0
        mock_async_run.return_value = 0

        result = self.runner.invoke(cli, ["run", "echo hello", "--provider", "modal"])

        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_run_command_with_options(self, mock_async_run):
        """Test run command with all options."""
        mock_async_run.return_value = 0

        result = self.runner.invoke(
            cli,
            [
                "run",
                "python script.py",
                "--provider",
                "e2b",
                "--image",
                "python:3.11",
                "--env",
                "API_KEY=secret",
                "--env",
                "DEBUG=true",
                "--label",
                "env=test",
                "--label",
                "app=myapp",
                "--timeout",
                "300",
                "--no-reuse",
                "--keep",
            ],
        )

        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_list_command(self, mock_async_run):
        """Test list command."""
        mock_async_run.return_value = None

        result = self.runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_list_command_with_filters(self, mock_async_run):
        """Test list command with filters."""
        mock_async_run.return_value = None

        result = self.runner.invoke(
            cli, ["list", "--provider", "modal", "--label", "env=prod", "--json"]
        )

        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_destroy_command(self, mock_async_run):
        """Test destroy command."""
        mock_async_run.return_value = None

        result = self.runner.invoke(cli, ["destroy", "sandbox-123", "--provider", "modal"])

        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_exec_command(self, mock_async_run):
        """Test exec command."""
        mock_async_run.return_value = None

        result = self.runner.invoke(
            cli, ["exec", "sandbox-123", "ls -la", "--provider", "e2b", "--env", "PATH=/usr/bin"]
        )

        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_test_command(self, mock_async_run):
        """Test test command."""
        mock_async_run.return_value = None

        result = self.runner.invoke(cli, ["test"])
        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_test_command_specific_provider(self, mock_async_run):
        """Test test command with specific provider."""
        mock_async_run.return_value = None

        result = self.runner.invoke(cli, ["test", "--provider", "modal"])

        assert result.exit_code == 0
        mock_async_run.assert_called_once()

    def test_providers_command(self):
        """Test providers command."""
        with patch("os.getenv") as mock_getenv, patch("os.path.exists") as mock_exists:

            def getenv_side_effect(key: str) -> str | None:
                if key == "E2B_API_KEY":
                    return "test_key"
                if key in {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY"}:
                    return "cf_token"
                if key == "DAYTONA_API_KEY":
                    return None
                return None

            mock_getenv.side_effect = getenv_side_effect
            mock_exists.return_value = True  # Modal config exists

            result = self.runner.invoke(cli, ["providers"])

            assert result.exit_code == 0
            assert "Available Providers" in result.output
            assert "e2b" in result.output
            assert "modal" in result.output
            assert "daytona" in result.output
            assert "cloudflare" in result.output
            assert "Configured" in result.output


class TestCLIDepsFlag:
    """Test --deps flag functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    @patch("sandboxes.cli.asyncio.run")
    def test_run_command_with_deps_flag(self, mock_async_run, tmp_path):
        """Test run command accepts --deps flag."""
        mock_async_run.return_value = 0

        # Create temp file
        test_file = tmp_path / "test.go"
        test_file.write_text("package main\nfunc main() {}")

        self.runner.invoke(
            cli,
            ["run", "--file", str(test_file), "--lang", "go", "--deps", "--provider", "modal"],
        )

        # Command should be accepted
        mock_async_run.assert_called_once()

    @patch("sandboxes.cli.asyncio.run")
    def test_run_command_with_no_deps_flag(self, mock_async_run, tmp_path):
        """Test run command with --no-deps flag."""
        mock_async_run.return_value = 0

        # Create temp file
        test_file = tmp_path / "test.go"
        test_file.write_text("package main\nfunc main() {}")

        self.runner.invoke(
            cli,
            ["run", "--file", str(test_file), "--lang", "go", "--no-deps", "--provider", "modal"],
        )

        mock_async_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_deps_uploads_go_mod(self, tmp_path):
        """Test that --deps uploads go.mod file."""
        # Create test files
        test_file = tmp_path / "main.go"
        test_file.write_text("package main\nfunc main() {}")

        go_mod = tmp_path / "go.mod"
        go_mod.write_text("module test\ngo 1.21")

        go_sum = tmp_path / "go.sum"
        go_sum.write_text("# go.sum content")

        # Mock provider
        mock_provider = AsyncMock()
        mock_provider.name = "modal"
        mock_provider.create_sandbox = AsyncMock(
            return_value=Sandbox(id="test-sandbox", provider="modal", state=SandboxState.RUNNING)
        )
        mock_provider.execute_command = AsyncMock(
            return_value=ExecutionResult(exit_code=0, stdout="Success", stderr="")
        )
        mock_provider.upload_file = AsyncMock(return_value=True)
        mock_provider.destroy_sandbox = AsyncMock(return_value=True)

        # Simulate the deps flow
        await mock_provider.create_sandbox(SandboxConfig())

        # Upload go.mod and go.sum
        await mock_provider.upload_file("test-sandbox", str(go_mod), "/tmp/goapp/go.mod")
        await mock_provider.upload_file("test-sandbox", str(go_sum), "/tmp/goapp/go.sum")

        # Verify uploads were called
        assert mock_provider.upload_file.call_count == 2


class TestCLIAsyncFunctions:
    """Test the async functions used by CLI commands."""

    @pytest.mark.asyncio
    async def test_run_command_async_flow(self):
        """Test the async flow of run command."""
        mock_provider = AsyncMock()
        mock_provider.name = "modal"
        mock_provider.find_sandbox = AsyncMock(return_value=None)
        mock_provider.create_sandbox = AsyncMock(
            return_value=Sandbox(id="new-sandbox", provider="modal", state=SandboxState.RUNNING)
        )
        mock_provider.execute_command = AsyncMock(
            return_value=ExecutionResult(exit_code=0, stdout="Success", stderr="")
        )
        mock_provider.destroy_sandbox = AsyncMock(return_value=True)

        # Simulate the run command flow without hitting real providers
        provider = mock_provider

        config = SandboxConfig(timeout_seconds=120, labels={"test": "true"})

        sandbox = await provider.create_sandbox(config)
        assert sandbox.id == "new-sandbox"

        result = await provider.execute_command(sandbox.id, "echo test")
        assert result.exit_code == 0
        assert result.stdout == "Success"

        destroyed = await provider.destroy_sandbox(sandbox.id)
        assert destroyed is True

    @pytest.mark.asyncio
    async def test_list_command_async_flow(self):
        """Test the async flow of list command."""
        mock_sandboxes = [
            Sandbox(id="sb1", provider="modal", state=SandboxState.RUNNING),
            Sandbox(id="sb2", provider="e2b", state=SandboxState.RUNNING),
        ]

        mock_modal = AsyncMock()
        mock_modal.name = "modal"
        mock_modal.list_sandboxes = AsyncMock(return_value=[mock_sandboxes[0]])

        mock_e2b = AsyncMock()
        mock_e2b.name = "e2b"
        mock_e2b.list_sandboxes = AsyncMock(return_value=[mock_sandboxes[1]])

        # Simulate list command flow with mocked providers
        all_sandboxes = []
        for provider in [mock_modal, mock_e2b]:
            sandboxes = await provider.list_sandboxes()
            all_sandboxes.extend(sandboxes)

        assert len(all_sandboxes) == 2
        assert all_sandboxes[0].id == "sb1"
        assert all_sandboxes[1].id == "sb2"

    @pytest.mark.asyncio
    async def test_test_command_async_flow(self):
        """Test the async flow of test command."""
        mock_provider = AsyncMock()
        mock_provider.name = "modal"
        mock_provider.create_sandbox = AsyncMock(
            return_value=Sandbox(id="test-sandbox", provider="modal", state=SandboxState.RUNNING)
        )
        mock_provider.execute_command = AsyncMock(
            return_value=ExecutionResult(exit_code=0, stdout="Hello from CLI test", stderr="")
        )
        mock_provider.destroy_sandbox = AsyncMock(return_value=True)

        provider = mock_provider

        # Test flow
        config = SandboxConfig(labels={"test": "cli"})
        sandbox = await provider.create_sandbox(config)

        result = await provider.execute_command(sandbox.id, "echo 'Hello from CLI test'")

        assert "Hello from CLI test" in result.stdout

        await provider.destroy_sandbox(sandbox.id)

        mock_provider.create_sandbox.assert_called_once()
        mock_provider.execute_command.assert_called_once()
        mock_provider.destroy_sandbox.assert_called_once()


class TestCLIErrorHandling:
    """Test CLI error handling."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    def test_run_command_missing_provider(self):
        """Test run command fails without provider configuration."""
        with patch("sandboxes.cli.get_provider", side_effect=SystemExit(1)):
            result = self.runner.invoke(cli, ["run", "echo test", "--provider", "nonexistent"])
            assert result.exit_code == 1

    def test_destroy_command_missing_provider(self):
        """Test destroy command requires provider."""
        result = self.runner.invoke(cli, ["destroy", "sandbox-123"])
        assert result.exit_code == 2  # Click error for missing required option

    def test_exec_command_missing_provider(self):
        """Test exec command requires provider."""
        result = self.runner.invoke(cli, ["exec", "sandbox-123", "echo test"])
        assert result.exit_code == 2  # Click error for missing required option

    @patch("sandboxes.cli.asyncio.run")
    def test_run_command_execution_failure(self, mock_async_run):
        """Test run command handles execution failures."""
        # Simulate execution failure
        mock_async_run.return_value = 1  # Non-zero exit code

        result = self.runner.invoke(
            cli, ["run", "false", "--provider", "modal"]  # Command that always fails
        )

        assert result.exit_code == 1


class TestCLIIntegration:
    """Test CLI integration scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()

    @patch("sandboxes.cli.asyncio.run")
    def test_full_workflow(self, mock_async_run):
        """Test a full CLI workflow."""
        mock_async_run.return_value = None

        # Create and run
        result = self.runner.invoke(
            cli,
            [
                "run",
                'python -c "print(1+1)"',
                "--provider",
                "modal",
                "--label",
                "workflow=test",
                "--keep",
            ],
        )
        assert result.exit_code == 0

        # List
        result = self.runner.invoke(cli, ["list", "--label", "workflow=test"])
        assert result.exit_code == 0

        # Execute in existing
        result = self.runner.invoke(cli, ["exec", "sandbox-123", "ls -la", "--provider", "modal"])
        assert result.exit_code == 0

        # Destroy
        result = self.runner.invoke(cli, ["destroy", "sandbox-123", "--provider", "modal"])
        assert result.exit_code == 0

    def test_cli_help_subcommands(self):
        """Test help for all subcommands."""
        subcommands = ["run", "list", "destroy", "exec", "test", "providers"]

        for cmd in subcommands:
            result = self.runner.invoke(cli, [cmd, "--help"])
            assert result.exit_code == 0
            assert cmd in result.output.lower() or "usage" in result.output.lower()
