"""Configuration dataclasses for Claude Code integration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClaudeCodeConfig:
    """Configuration for Claude Code execution.

    Attributes:
        model: Claude model to use (default: claude-sonnet-4-20250514)
        timeout: Execution timeout in seconds
        working_dir: Working directory in sandbox
        anthropic_api_key: API key (uses ANTHROPIC_API_KEY env var if None)
        additional_env_vars: Extra environment variables
        provider: Sandbox provider to use (auto-detect if None)
        image: Provider-specific image/template
        labels: Labels for sandbox identification/reuse
        reuse_sandbox: Whether to reuse existing sandbox with matching labels
        print_mode: Use --print flag for non-interactive output
    """

    # Claude Code settings
    model: str = "claude-sonnet-4-20250514"

    # Execution settings
    timeout: int = 300  # 5 minutes default
    working_dir: str = "/home/user"
    print_mode: bool = True  # Use --print for non-interactive mode

    # Environment
    anthropic_api_key: str | None = None
    additional_env_vars: dict[str, str] = field(default_factory=dict)

    # Provider settings
    provider: str | None = None
    image: str | None = None

    # Sandbox reuse
    labels: dict[str, str] | None = None
    reuse_sandbox: bool = False

    def get_env_vars(self) -> dict[str, str]:
        """Get all environment variables for the sandbox."""
        env_vars = dict(self.additional_env_vars)
        if self.anthropic_api_key:
            env_vars["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        return env_vars

    def get_claude_command(self, prompt: str) -> str:
        """Build the Claude Code CLI command."""
        parts = ["claude"]

        if self.print_mode:
            parts.append("--print")

        # Skip permissions in sandbox environments for non-interactive use
        parts.append("--dangerously-skip-permissions")

        if self.model:
            parts.extend(["--model", self.model])

        # Add the prompt as positional argument (escape single quotes)
        escaped_prompt = prompt.replace("'", "'\\''")
        parts.append(f"'{escaped_prompt}'")

        return " ".join(parts)


@dataclass
class AgentConfig(ClaudeCodeConfig):
    """Extended configuration for ClaudeCodeAgent lifecycle.

    Inherits all settings from ClaudeCodeConfig plus:

    Attributes:
        conversation_history: Track conversation turns
        max_conversation_turns: Maximum turns before reset
        sandbox_ttl: Sandbox time-to-live in seconds
        auto_cleanup: Automatically destroy sandbox on exit
        persist_files: Keep files between turns
    """

    # Conversation settings
    conversation_history: bool = True
    max_conversation_turns: int = 100

    # Sandbox lifecycle
    sandbox_ttl: int = 3600  # 1 hour default
    auto_cleanup: bool = True

    # Persistence
    persist_files: bool = True


@dataclass
class SDKConfig(ClaudeCodeConfig):
    """Configuration for SDK integration.

    Inherits all settings from ClaudeCodeConfig plus:

    Attributes:
        allowed_tools: List of tools to enable (None = all)
        blocked_tools: List of tools to block
        max_iterations: Maximum tool-use iterations
        auto_install_sdk: Install claude-agent-sdk if not present
    """

    # Tool configuration
    allowed_tools: list[str] | None = None
    blocked_tools: list[str] = field(default_factory=list)

    # Execution
    max_iterations: int = 10

    # SDK settings
    auto_install_sdk: bool = True
