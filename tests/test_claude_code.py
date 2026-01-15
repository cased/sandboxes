"""Unit tests for Claude Code integration (mocked)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandboxes.claude import (
    AgentConfig,
    ClaudeCodeAgent,
    ClaudeCodeConfig,
    ClaudeCodeResult,
    ClaudeCodeSDK,
    run_claude_code,
)
from sandboxes.claude.exceptions import ClaudeCodeAPIKeyError
from sandboxes.claude.setup import (
    get_provider_config,
    get_recommended_template,
    get_setup_commands,
)


class TestClaudeCodeConfig:
    """Tests for configuration dataclass."""

    def test_default_config(self):
        config = ClaudeCodeConfig()
        assert config.model == "claude-sonnet-4-20250514"
        assert config.timeout == 300
        assert config.working_dir == "/home/user"
        assert config.print_mode is True

    def test_custom_config(self):
        config = ClaudeCodeConfig(
            model="claude-opus-4-20250514",
            timeout=600,
            working_dir="/workspace",
        )
        assert config.model == "claude-opus-4-20250514"
        assert config.timeout == 600
        assert config.working_dir == "/workspace"

    def test_get_env_vars(self):
        config = ClaudeCodeConfig(
            anthropic_api_key="test-key",
            additional_env_vars={"FOO": "bar"},
        )
        env_vars = config.get_env_vars()
        assert env_vars["ANTHROPIC_API_KEY"] == "test-key"
        assert env_vars["FOO"] == "bar"

    def test_get_claude_command(self):
        config = ClaudeCodeConfig(model="claude-sonnet-4-20250514")
        command = config.get_claude_command("Hello world")
        assert "claude" in command
        assert "--print" in command
        assert "--model" in command
        assert "claude-sonnet-4-20250514" in command
        assert "'Hello world'" in command

    def test_get_claude_command_escapes_quotes(self):
        config = ClaudeCodeConfig()
        command = config.get_claude_command("Say 'hello'")
        # Single quotes in prompt should be escaped
        assert "'\\''" in command or "'\\''hello'\\'''" in command


class TestAgentConfig:
    """Tests for agent configuration."""

    def test_inherits_from_claude_config(self):
        config = AgentConfig()
        assert hasattr(config, "model")
        assert hasattr(config, "timeout")
        assert hasattr(config, "conversation_history")

    def test_agent_specific_defaults(self):
        config = AgentConfig()
        assert config.conversation_history is True
        assert config.max_conversation_turns == 100
        assert config.sandbox_ttl == 3600
        assert config.auto_cleanup is True


class TestClaudeCodeResult:
    """Tests for result dataclass."""

    def test_success_property(self):
        result = ClaudeCodeResult(output="Hello", exit_code=0)
        assert result.success is True

        result = ClaudeCodeResult(output="Error", exit_code=1)
        assert result.success is False

    def test_str_returns_output(self):
        result = ClaudeCodeResult(output="Hello world", exit_code=0)
        assert str(result) == "Hello world"


class TestProviderSetup:
    """Tests for provider-specific setup logic."""

    def test_get_provider_config_e2b(self):
        config = get_provider_config("e2b")
        assert config["template"] == "anthropic-claude-code"
        # E2B has update commands to keep Claude Code current
        assert len(config["setup_commands"]) > 0

    def test_get_provider_config_modal(self):
        config = get_provider_config("modal")
        assert config["template"] is None
        # Modal uses prebaked images, so no setup commands needed
        assert config["prebaked"] is True
        assert config["image_builder"] == "get_modal_claude_image"

    def test_get_provider_config_unknown(self):
        config = get_provider_config("unknown-provider")
        # Should return default config
        assert "setup_commands" in config

    def test_get_recommended_template(self):
        assert get_recommended_template("e2b") == "anthropic-claude-code"
        assert get_recommended_template("modal") is None
        assert get_recommended_template("daytona") is None

    def test_get_setup_commands(self):
        # Modal uses prebaked images, so no setup commands
        commands = get_setup_commands("modal")
        assert len(commands) == 0

        # E2B has update commands to keep Claude Code current
        commands = get_setup_commands("e2b")
        assert len(commands) > 0

        # Daytona still has setup commands
        commands = get_setup_commands("daytona")
        assert len(commands) > 0


class TestRunClaudeCode:
    """Tests for run_claude_code function."""

    @pytest.mark.asyncio
    async def test_requires_api_key(self):
        """Should raise error if no API key provided."""
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ClaudeCodeAPIKeyError):
            await run_claude_code("Hello")

    @pytest.mark.asyncio
    async def test_simple_execution(self):
        """Test basic prompt execution with mocked sandbox."""
        with (
            patch("sandboxes.claude.runner.Sandbox") as mock_sandbox_cls,
            patch(
                "sandboxes.claude.runner.setup_claude_code", new_callable=AsyncMock
            ) as mock_setup,
        ):
            # Setup mocks
            mock_sandbox = AsyncMock()
            mock_sandbox.id = "test-sandbox-123"
            mock_sandbox.execute = AsyncMock(
                return_value=MagicMock(
                    exit_code=0,
                    stdout="Hello World",
                    stderr="",
                    success=True,
                )
            )
            mock_sandbox.destroy = AsyncMock()

            # Make create return an awaitable that resolves to mock_sandbox
            mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
            mock_setup.return_value = True

            result = await run_claude_code(
                "Print hello world",
                anthropic_api_key="test-key",
            )

            assert result.success
            assert "Hello World" in result.output
            assert result.sandbox_id == "test-sandbox-123"

            # Verify cleanup
            mock_sandbox.destroy.assert_called_once()


class TestClaudeCodeAgent:
    """Tests for ClaudeCodeAgent class."""

    def test_requires_api_key(self):
        """Should raise error if no API key provided."""
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ClaudeCodeAPIKeyError):
            ClaudeCodeAgent()

    def test_init_with_api_key(self):
        """Should initialize with provided API key."""
        agent = ClaudeCodeAgent(anthropic_api_key="test-key")
        assert agent.config.anthropic_api_key == "test-key"
        assert agent.is_running is False
        assert agent.sandbox is None

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        """Test agent start/stop lifecycle."""
        with (
            patch("sandboxes.claude.agent.Sandbox") as mock_sandbox_cls,
            patch("sandboxes.claude.agent.setup_claude_code", new_callable=AsyncMock) as mock_setup,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.id = "agent-sandbox-123"
            mock_sandbox.destroy = AsyncMock()
            mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
            mock_setup.return_value = True

            agent = ClaudeCodeAgent(anthropic_api_key="test-key")

            # Start
            await agent.start()
            assert agent.is_running is True
            assert agent.sandbox is not None

            # Stop
            await agent.stop()
            assert agent.is_running is False
            mock_sandbox.destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async with context manager."""
        with (
            patch("sandboxes.claude.agent.Sandbox") as mock_sandbox_cls,
            patch("sandboxes.claude.agent.setup_claude_code", new_callable=AsyncMock) as mock_setup,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.id = "ctx-sandbox-123"
            mock_sandbox.execute = AsyncMock(
                return_value=MagicMock(
                    exit_code=0,
                    stdout="Test output",
                    stderr="",
                    success=True,
                )
            )
            mock_sandbox.destroy = AsyncMock()
            mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
            mock_setup.return_value = True

            async with ClaudeCodeAgent(anthropic_api_key="test-key") as agent:
                assert agent.is_running is True
                result = await agent.run("Test prompt")
                assert result.success

            # Should be stopped after exiting context
            mock_sandbox.destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_conversation_history(self):
        """Test conversation history tracking."""
        with (
            patch("sandboxes.claude.agent.Sandbox") as mock_sandbox_cls,
            patch("sandboxes.claude.agent.setup_claude_code", new_callable=AsyncMock) as mock_setup,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.id = "history-sandbox"
            mock_sandbox.execute = AsyncMock(
                return_value=MagicMock(
                    exit_code=0,
                    stdout="Response",
                    stderr="",
                    success=True,
                )
            )
            mock_sandbox.destroy = AsyncMock()
            mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
            mock_setup.return_value = True

            async with ClaudeCodeAgent(anthropic_api_key="test-key") as agent:
                await agent.run("First prompt")
                await agent.run("Second prompt")

                history = agent.conversation_history
                assert len(history) == 2
                assert history[0].prompt == "First prompt"
                assert history[1].prompt == "Second prompt"
                assert history[0].turn_number == 1
                assert history[1].turn_number == 2

    @pytest.mark.asyncio
    async def test_reset_clears_history(self):
        """Test reset() clears conversation history."""
        with (
            patch("sandboxes.claude.agent.Sandbox") as mock_sandbox_cls,
            patch("sandboxes.claude.agent.setup_claude_code", new_callable=AsyncMock) as mock_setup,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.id = "reset-sandbox"
            mock_sandbox.execute = AsyncMock(
                return_value=MagicMock(
                    exit_code=0,
                    stdout="Response",
                    stderr="",
                    success=True,
                )
            )
            mock_sandbox.destroy = AsyncMock()
            mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
            mock_setup.return_value = True

            async with ClaudeCodeAgent(anthropic_api_key="test-key") as agent:
                await agent.run("Prompt")
                assert len(agent.conversation_history) == 1

                await agent.reset()
                assert len(agent.conversation_history) == 0


class TestClaudeCodeSDK:
    """Tests for ClaudeCodeSDK class."""

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        """Test SDK start/stop lifecycle."""
        with (
            patch("sandboxes.claude.agent.Sandbox") as mock_sandbox_cls,
            patch("sandboxes.claude.agent.setup_claude_code", new_callable=AsyncMock) as mock_setup,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.id = "sdk-sandbox"
            mock_sandbox.execute = AsyncMock(
                return_value=MagicMock(
                    exit_code=0,
                    stdout="SDK output",
                    stderr="",
                    success=True,
                )
            )
            mock_sandbox.destroy = AsyncMock()
            mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
            mock_setup.return_value = True

            async with ClaudeCodeSDK(anthropic_api_key="test-key") as sdk:
                assert sdk.agent is not None
                assert sdk.agent.is_running is True

    @pytest.mark.asyncio
    async def test_run_agent(self):
        """Test run_agent method."""
        with (
            patch("sandboxes.claude.agent.Sandbox") as mock_sandbox_cls,
            patch("sandboxes.claude.agent.setup_claude_code", new_callable=AsyncMock) as mock_setup,
        ):
            mock_sandbox = AsyncMock()
            mock_sandbox.id = "run-agent-sandbox"
            mock_sandbox.execute = AsyncMock(
                return_value=MagicMock(
                    exit_code=0,
                    stdout="Agent output",
                    stderr="",
                    success=True,
                )
            )
            mock_sandbox.destroy = AsyncMock()
            mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
            mock_setup.return_value = True

            async with ClaudeCodeSDK(anthropic_api_key="test-key") as sdk:
                result = await sdk.run_agent(
                    "Create a file",
                    tools=["Write", "Bash"],
                )
                assert result.success
                assert "Agent output" in result.output
