"""Layer 2: Claude Code Agent with persistent sandbox lifecycle."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from ..base import ExecutionResult
from ..sandbox import Sandbox
from .config import AgentConfig
from .exceptions import ClaudeCodeAPIKeyError, ClaudeCodeError
from .result import ClaudeCodeResult, ConversationTurn
from .setup import get_modal_claude_image, get_recommended_template, setup_claude_code

logger = logging.getLogger(__name__)


class ClaudeCodeAgent:
    """Claude Code Agent with persistent sandbox and conversation state.

    Manages a long-running sandbox for multi-turn conversations with
    Claude Code. Files persist between prompts, allowing iterative
    development workflows.

    Example:
        >>> async with ClaudeCodeAgent() as agent:
        ...     # First prompt - create initial code
        ...     await agent.run("Create a Flask app in app.py")
        ...
        ...     # Second prompt - modify based on context
        ...     await agent.run("Add a /health endpoint")
        ...
        ...     # Third prompt - Claude sees previous work
        ...     await agent.run("Write tests for all endpoints")
        ...
        ...     # Download the results
        ...     await agent.download("/home/user/app.py", "./app.py")

    Attributes:
        config: Agent configuration
        sandbox: The underlying sandbox (None until started)
        conversation_history: List of all conversation turns
        is_running: Whether the agent is currently running
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        *,
        # Convenience kwargs
        provider: str | None = None,
        anthropic_api_key: str | None = None,
        model: str | None = None,
        labels: dict[str, str] | None = None,
        timeout: int | None = None,
        working_dir: str | None = None,
        **kwargs: Any,
    ):
        """Initialize the Claude Code Agent.

        Args:
            config: Full agent configuration
            provider: Sandbox provider to use
            anthropic_api_key: Anthropic API key
            model: Claude model to use
            labels: Labels for sandbox identification/reuse
            timeout: Default timeout for executions
            working_dir: Working directory in sandbox
            **kwargs: Additional config options
        """
        # Build config
        if config is None:
            config = AgentConfig(**kwargs)

        # Apply overrides
        if provider is not None:
            config.provider = provider
        if model is not None:
            config.model = model
        if labels is not None:
            config.labels = labels
        if timeout is not None:
            config.timeout = timeout
        if working_dir is not None:
            config.working_dir = working_dir

        # Get API key
        api_key = anthropic_api_key or config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ClaudeCodeAPIKeyError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY environment variable "
                "or pass anthropic_api_key parameter."
            )
        config.anthropic_api_key = api_key

        self.config = config
        self._sandbox: Sandbox | None = None
        self._conversation_history: list[ConversationTurn] = []
        self._turn_number = 0
        self._is_running = False

    @property
    def sandbox(self) -> Sandbox | None:
        """The underlying sandbox instance."""
        return self._sandbox

    @property
    def conversation_history(self) -> list[ConversationTurn]:
        """List of all conversation turns."""
        return self._conversation_history.copy()

    @property
    def is_running(self) -> bool:
        """Whether the agent is currently running."""
        return self._is_running and self._sandbox is not None

    async def start(self) -> None:
        """Start the agent and create the sandbox.

        Called automatically when using async with, but can be
        called manually for explicit lifecycle control.

        Raises:
            ClaudeCodeError: If sandbox creation fails
        """
        if self._is_running:
            logger.warning("Agent already running")
            return

        logger.info("Starting Claude Code Agent")

        # Determine template/image
        provider_name = self.config.provider
        image = (
            self.config.image or get_recommended_template(provider_name) if provider_name else None
        )

        # For Modal, use prebaked image with Claude Code installed
        if provider_name == "modal" and not self.config.image:
            try:
                image = get_modal_claude_image()
                logger.info("Using prebaked Modal image with Claude Code")
            except ImportError:
                logger.warning("Modal not available, will install Claude Code at runtime")

        # Build sandbox config
        sandbox_kwargs: dict[str, Any] = {
            "env_vars": self.config.get_env_vars(),
        }
        if provider_name:
            sandbox_kwargs["provider"] = provider_name
        if image:
            sandbox_kwargs["image"] = image
        if self.config.labels:
            sandbox_kwargs["labels"] = self.config.labels

        # Create or reuse sandbox
        if self.config.reuse_sandbox and self.config.labels:
            self._sandbox = await Sandbox.get_or_create(**sandbox_kwargs)
        else:
            self._sandbox = await Sandbox.create(**sandbox_kwargs)

        # Setup Claude Code
        await setup_claude_code(
            self._sandbox,
            provider_name=provider_name,
            anthropic_api_key=self.config.anthropic_api_key,
        )

        self._is_running = True
        logger.info(f"Agent started with sandbox {self._sandbox.id}")

    async def stop(self) -> None:
        """Stop the agent and destroy the sandbox.

        Called automatically when exiting async with context.
        """
        if not self._is_running:
            return

        logger.info("Stopping Claude Code Agent")

        if self._sandbox and self.config.auto_cleanup:
            try:
                await self._sandbox.destroy()
            except Exception as e:
                logger.warning(f"Failed to destroy sandbox: {e}")

        self._sandbox = None
        self._is_running = False

    async def run(
        self,
        prompt: str,
        *,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ClaudeCodeResult:
        """Execute a prompt in the persistent sandbox.

        Args:
            prompt: The prompt to send to Claude Code
            timeout: Override timeout for this execution
            env_vars: Additional environment variables for this execution

        Returns:
            ClaudeCodeResult with output and artifacts

        Raises:
            ClaudeCodeError: If agent not started or execution fails
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None

        self._turn_number += 1
        start_time = time.time()

        # Set up additional env vars for this execution
        if env_vars:
            for key, value in env_vars.items():
                escaped = value.replace("'", "'\\''")
                await self._sandbox.execute(f"export {key}='{escaped}'")

        # Build and execute command
        command = self.config.get_claude_command(prompt)
        execution_timeout = timeout or self.config.timeout

        result = await self._sandbox.execute(command, timeout=execution_timeout)

        duration_ms = int((time.time() - start_time) * 1000)

        claude_result = ClaudeCodeResult(
            output=result.stdout.strip(),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            model=self.config.model,
            sandbox_id=self._sandbox.id,
        )

        # Record conversation turn
        if self.config.conversation_history:
            turn = ConversationTurn(
                prompt=prompt,
                result=claude_result,
                timestamp=start_time,
                turn_number=self._turn_number,
            )
            self._conversation_history.append(turn)

            # Check max turns
            if len(self._conversation_history) >= self.config.max_conversation_turns:
                logger.warning(
                    f"Reached max conversation turns ({self.config.max_conversation_turns})"
                )

        return claude_result

    async def stream(
        self,
        prompt: str,
        *,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream Claude Code output for a prompt.

        Yields output chunks as they are generated.

        Args:
            prompt: The prompt to send to Claude Code
            timeout: Override timeout for this execution
            **kwargs: Additional arguments

        Yields:
            Output chunks as strings
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None

        command = self.config.get_claude_command(prompt)

        async for chunk in self._sandbox.stream(command):
            yield chunk

    async def upload(
        self,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Upload a file to the agent's sandbox.

        Args:
            local_path: Path to local file
            remote_path: Destination path in sandbox

        Returns:
            True if upload succeeded
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None
        return await self._sandbox.upload(local_path, remote_path)

    async def upload_content(
        self,
        content: str | bytes,
        remote_path: str,
    ) -> bool:
        """Upload content directly to a path in the sandbox.

        Args:
            content: File content (string or bytes)
            remote_path: Destination path in sandbox

        Returns:
            True if upload succeeded
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None

        if isinstance(content, str):
            content = content.encode("utf-8")

        import base64

        encoded = base64.b64encode(content).decode("utf-8")

        # Create directory if needed
        dir_path = "/".join(remote_path.rsplit("/", 1)[:-1])
        if dir_path:
            await self._sandbox.execute(f"mkdir -p {dir_path}")

        result = await self._sandbox.execute(f"echo '{encoded}' | base64 -d > {remote_path}")
        return result.success

    async def download(
        self,
        remote_path: str,
        local_path: str,
    ) -> bool:
        """Download a file from the agent's sandbox.

        Args:
            remote_path: Path in sandbox
            local_path: Destination path locally

        Returns:
            True if download succeeded
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None
        return await self._sandbox.download(remote_path, local_path)

    async def execute(
        self,
        command: str,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute a raw shell command in the sandbox.

        Args:
            command: Shell command to execute
            **kwargs: Additional arguments passed to sandbox.execute

        Returns:
            ExecutionResult from the sandbox
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None
        return await self._sandbox.execute(command, **kwargs)

    async def list_files(
        self,
        path: str = "/home/user",
    ) -> list[str]:
        """List files in the sandbox.

        Args:
            path: Directory to list

        Returns:
            List of file paths
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None

        result = await self._sandbox.execute(f"find {path} -type f 2>/dev/null")
        if result.success:
            return [f for f in result.stdout.strip().split("\n") if f]
        return []

    async def read_file(
        self,
        path: str,
    ) -> str:
        """Read a file from the sandbox.

        Args:
            path: File path in sandbox

        Returns:
            File contents as string
        """
        if not self.is_running:
            raise ClaudeCodeError("Agent not running. Call start() or use async with.")

        assert self._sandbox is not None

        result = await self._sandbox.execute(f"cat {path}")
        if result.success:
            return result.stdout
        raise ClaudeCodeError(f"Failed to read file {path}: {result.stderr}")

    async def reset(self) -> None:
        """Reset conversation history (sandbox persists)."""
        self._conversation_history.clear()
        self._turn_number = 0
        logger.info("Conversation history reset")

    async def restart(self) -> None:
        """Destroy and recreate the sandbox."""
        await self.stop()
        await self.start()

    # Context manager support
    async def __aenter__(self) -> ClaudeCodeAgent:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()
