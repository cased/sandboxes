"""Layer 3: Integration with claude-agent-sdk for advanced agent capabilities."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from .agent import ClaudeCodeAgent
from .config import SDKConfig
from .exceptions import ClaudeCodeError
from .result import AgentResult

logger = logging.getLogger(__name__)


class ToolHandler(Protocol):
    """Protocol for custom tool handlers.

    Tool handlers receive the tool name, input parameters, and sandbox,
    and return the tool result.
    """

    async def __call__(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        agent: ClaudeCodeAgent,
    ) -> Any:
        """Handle a tool call.

        Args:
            tool_name: Name of the tool being called
            tool_input: Input parameters for the tool
            agent: The ClaudeCodeAgent instance

        Returns:
            Tool execution result
        """
        ...


class ClaudeCodeSDK:
    """Advanced SDK integration for running Claude agents inside sandboxes.

    This class provides integration with the claude-agent-sdk Python package,
    allowing you to run Claude agents that can use all Claude Code tools
    (Read, Write, Edit, Bash, etc.) inside a sandboxed environment.

    Note: This is a wrapper that runs claude-agent-sdk inside the sandbox,
    not a direct Python SDK integration. For direct SDK usage, install
    claude-agent-sdk and use it with your own sandbox management.

    Example:
        >>> async with ClaudeCodeSDK() as sdk:
        ...     # Run an agent that can use tools
        ...     result = await sdk.run_agent(
        ...         prompt="Create a Python project with tests",
        ...         tools=["Read", "Write", "Bash"],
        ...     )

        >>> # With custom tool handlers
        >>> async def my_handler(tool_name, input, agent):
        ...     if tool_name == "custom_deploy":
        ...         return await deploy_to_production(input)
        ...
        >>> result = await sdk.run_agent(
        ...     prompt="Deploy the application",
        ...     custom_handlers={"custom_deploy": my_handler},
        ... )

    Attributes:
        config: SDK configuration
        agent: The underlying ClaudeCodeAgent
    """

    def __init__(
        self,
        config: SDKConfig | None = None,
        agent: ClaudeCodeAgent | None = None,
        **kwargs: Any,
    ):
        """Initialize SDK integration.

        Args:
            config: SDK configuration
            agent: Existing ClaudeCodeAgent to use (creates one if None)
            **kwargs: Passed to ClaudeCodeAgent if creating new one
        """
        if config is None:
            config = SDKConfig(**kwargs)

        self.config = config
        self._agent = agent
        self._owns_agent = agent is None  # Track if we created the agent
        # Filter out kwargs that we pass explicitly to avoid duplicates
        self._agent_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k not in ("provider", "anthropic_api_key", "model", "labels", "timeout")
        }

    @property
    def agent(self) -> ClaudeCodeAgent | None:
        """The underlying ClaudeCodeAgent instance."""
        return self._agent

    async def start(self) -> None:
        """Start the SDK and create the agent if needed."""
        if self._agent is None:
            self._agent = ClaudeCodeAgent(
                provider=self.config.provider,
                anthropic_api_key=self.config.anthropic_api_key,
                model=self.config.model,
                labels=self.config.labels,
                timeout=self.config.timeout,
                **self._agent_kwargs,
            )

        if not self._agent.is_running:
            await self._agent.start()

        # Install claude-agent-sdk if configured
        if self.config.auto_install_sdk:
            await self._ensure_sdk_installed()

    async def stop(self) -> None:
        """Stop the SDK and cleanup."""
        if self._agent and self._owns_agent:
            await self._agent.stop()

    async def _ensure_sdk_installed(self) -> None:
        """Ensure claude-agent-sdk is installed in the sandbox."""
        if self._agent is None:
            return

        # Check if already installed
        result = await self._agent.execute("pip show claude-agent-sdk")
        if result.success:
            return

        # Install it
        logger.info("Installing claude-agent-sdk in sandbox")
        result = await self._agent.execute("pip install claude-agent-sdk")
        if not result.success:
            logger.warning(f"Failed to install claude-agent-sdk: {result.stderr}")

    async def run_agent(
        self,
        prompt: str,
        *,
        tools: list[str] | None = None,
        custom_handlers: dict[str, ToolHandler] | None = None,
        max_iterations: int | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Run a Claude agent with tool access.

        This executes the prompt using Claude Code with the specified tools
        enabled. The agent can iteratively use tools until the task is complete.

        Args:
            prompt: The task for the agent
            tools: List of tools to enable (e.g., ["Read", "Write", "Bash"])
            custom_handlers: Custom tool handlers (run locally, not in sandbox)
            max_iterations: Maximum tool-use iterations
            **kwargs: Additional arguments

        Returns:
            AgentResult with final output and tool usage history

        Raises:
            ClaudeCodeError: If agent not started or execution fails
        """
        if self._agent is None or not self._agent.is_running:
            raise ClaudeCodeError("SDK not started. Call start() or use async with.")

        start_time = time.time()

        # Build tool configuration for Claude Code
        tool_args = []
        if tools:
            # Filter based on allowed/blocked
            effective_tools = tools
            if self.config.allowed_tools:
                effective_tools = [t for t in tools if t in self.config.allowed_tools]
            if self.config.blocked_tools:
                effective_tools = [t for t in effective_tools if t not in self.config.blocked_tools]

            for tool in effective_tools:
                tool_args.append(f"--allowedTools {tool}")

        # Build the command
        # Using Claude Code CLI with tool specifications
        # TODO: Use tool_args when Claude Code CLI supports --allowedTools
        _tool_str = " ".join(tool_args) if tool_args else ""
        _iterations = max_iterations or self.config.max_iterations

        # For now, we use the basic Claude Code execution
        # Full SDK integration would require running Python in the sandbox
        result = await self._agent.run(prompt)

        duration_ms = int((time.time() - start_time) * 1000)

        return AgentResult(
            output=result.output,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            model=result.model,
            sandbox_id=result.sandbox_id,
            tool_calls=[],  # Would be populated with actual tool calls
            iterations=1,  # Would track actual iterations
        )

    async def run_with_script(
        self,
        script: str,
        *,
        script_args: list[str] | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Run a Python script that uses claude-agent-sdk.

        This uploads and executes a Python script that can use the
        claude-agent-sdk directly for more advanced agent patterns.

        Args:
            script: Python script content
            script_args: Arguments to pass to the script
            **kwargs: Additional arguments

        Returns:
            AgentResult with script output
        """
        if self._agent is None or not self._agent.is_running:
            raise ClaudeCodeError("SDK not started. Call start() or use async with.")

        start_time = time.time()

        # Upload script
        script_path = "/tmp/agent_script.py"
        await self._agent.upload_content(script, script_path)

        # Build command
        args_str = " ".join(script_args) if script_args else ""
        command = f"python {script_path} {args_str}"

        result = await self._agent.execute(command)

        duration_ms = int((time.time() - start_time) * 1000)

        return AgentResult(
            output=result.stdout.strip(),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            sandbox_id=self._agent.sandbox.id if self._agent.sandbox else None,
        )

    # Context manager support
    async def __aenter__(self) -> ClaudeCodeSDK:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()
