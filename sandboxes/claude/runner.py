"""Layer 1: Simple helper functions for Claude Code execution."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from ..sandbox import Sandbox
from .config import ClaudeCodeConfig
from .exceptions import ClaudeCodeAPIKeyError, ClaudeCodeError
from .result import ClaudeCodeResult
from .setup import get_modal_claude_image, get_recommended_template, setup_claude_code

logger = logging.getLogger(__name__)


async def run_claude_code(
    prompt: str,
    *,
    config: ClaudeCodeConfig | None = None,
    # Convenience kwargs (override config)
    model: str | None = None,
    timeout: int | None = None,
    provider: str | None = None,
    anthropic_api_key: str | None = None,
    env_vars: dict[str, str] | None = None,
    working_dir: str | None = None,
    files: dict[str, str | bytes] | None = None,
    labels: dict[str, str] | None = None,
    reuse_sandbox: bool = False,
    **kwargs: Any,
) -> ClaudeCodeResult:
    """Execute a single Claude Code prompt in a sandbox.

    This is a high-level convenience function that:
    1. Creates a sandbox with Claude Code installed
    2. Uploads any provided files
    3. Executes the prompt
    4. Returns the result and cleans up

    Args:
        prompt: The prompt to send to Claude Code
        config: Full configuration object (optional)
        model: Claude model to use
        timeout: Execution timeout in seconds
        provider: Sandbox provider to use
        anthropic_api_key: API key (or uses ANTHROPIC_API_KEY env var)
        env_vars: Additional environment variables
        working_dir: Working directory for Claude Code
        files: Files to upload before execution {remote_path: content}
        labels: Labels for sandbox identification
        reuse_sandbox: Reuse existing sandbox with matching labels
        **kwargs: Additional arguments passed to ClaudeCodeConfig

    Returns:
        ClaudeCodeResult with output, artifacts, and metadata

    Raises:
        ClaudeCodeError: If execution fails
        ClaudeCodeAPIKeyError: If API key is not provided

    Example:
        >>> result = await run_claude_code("Create a hello.py that prints 'Hello World'")
        >>> print(result.output)

        >>> result = await run_claude_code(
        ...     "Analyze this data",
        ...     files={"/home/user/data.csv": csv_content},
        ...     provider="e2b",
        ... )
    """
    # Build config from kwargs
    if config is None:
        config = ClaudeCodeConfig(**kwargs)

    # Override config with explicit kwargs
    if model is not None:
        config.model = model
    if timeout is not None:
        config.timeout = timeout
    if provider is not None:
        config.provider = provider
    if working_dir is not None:
        config.working_dir = working_dir
    if labels is not None:
        config.labels = labels
    if reuse_sandbox:
        config.reuse_sandbox = reuse_sandbox
    if env_vars:
        config.additional_env_vars.update(env_vars)

    # Get API key
    api_key = anthropic_api_key or config.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ClaudeCodeAPIKeyError(
            "Anthropic API key required. Set ANTHROPIC_API_KEY environment variable "
            "or pass anthropic_api_key parameter."
        )
    config.anthropic_api_key = api_key

    # Determine provider and template/image
    provider_name = config.provider
    image = config.image or get_recommended_template(provider_name) if provider_name else None

    # For Modal, use prebaked image with Claude Code installed
    if provider_name == "modal" and not config.image:
        try:
            image = get_modal_claude_image()
            logger.info("Using prebaked Modal image with Claude Code")
        except ImportError:
            logger.warning("Modal not available, will install Claude Code at runtime")

    # Build sandbox config
    sandbox_kwargs: dict[str, Any] = {
        "env_vars": config.get_env_vars(),
        "timeout": config.timeout,  # Pass timeout to sandbox creation
    }
    if provider_name:
        sandbox_kwargs["provider"] = provider_name
    if image:
        sandbox_kwargs["image"] = image
    if config.labels:
        sandbox_kwargs["labels"] = config.labels

    start_time = time.time()

    # Create or reuse sandbox
    if config.reuse_sandbox and config.labels:
        sandbox = await Sandbox.get_or_create(**sandbox_kwargs)
    else:
        sandbox = await Sandbox.create(**sandbox_kwargs)

    try:
        # Setup Claude Code if needed
        await setup_claude_code(
            sandbox,
            provider_name=provider_name,
            anthropic_api_key=api_key,
        )

        # Upload files if provided
        if files:
            for remote_path, content in files.items():
                if isinstance(content, str):
                    content = content.encode("utf-8")
                # Use execute to write file content via base64
                import base64

                encoded = base64.b64encode(content).decode("utf-8")
                dir_path = "/".join(remote_path.rsplit("/", 1)[:-1])
                if dir_path:
                    await sandbox.execute(f"mkdir -p {dir_path}")
                await sandbox.execute(f"echo '{encoded}' | base64 -d > {remote_path}")

        # Change to working directory
        if config.working_dir:
            await sandbox.execute(f"cd {config.working_dir}")

        # Build and execute Claude Code command
        command = config.get_claude_command(prompt)
        logger.debug(f"Executing: {command}")

        result = await sandbox.execute(command, timeout=config.timeout)

        duration_ms = int((time.time() - start_time) * 1000)

        return ClaudeCodeResult(
            output=result.stdout.strip(),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            model=config.model,
            sandbox_id=sandbox.id,
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        if isinstance(e, ClaudeCodeError):
            raise
        raise ClaudeCodeError(f"Claude Code execution failed: {e}") from e

    finally:
        # Cleanup unless reusing
        if not config.reuse_sandbox:
            try:
                await sandbox.destroy()
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup sandbox: {cleanup_error}")


async def stream_claude_code(
    prompt: str,
    *,
    config: ClaudeCodeConfig | None = None,
    **kwargs: Any,
) -> AsyncIterator[str]:
    """Stream Claude Code output in real-time.

    Yields output chunks as Claude Code generates them.

    Args:
        prompt: The prompt to send to Claude Code
        config: Full configuration object (optional)
        **kwargs: Arguments passed to run_claude_code

    Yields:
        Output chunks as strings

    Example:
        >>> async for chunk in stream_claude_code("Build a web scraper"):
        ...     print(chunk, end="", flush=True)
    """
    # Build config
    if config is None:
        config = ClaudeCodeConfig(**kwargs)

    # Get API key
    api_key = (
        kwargs.get("anthropic_api_key")
        or config.anthropic_api_key
        or os.getenv("ANTHROPIC_API_KEY")
    )
    if not api_key:
        raise ClaudeCodeAPIKeyError(
            "Anthropic API key required. Set ANTHROPIC_API_KEY environment variable "
            "or pass anthropic_api_key parameter."
        )
    config.anthropic_api_key = api_key

    # Determine provider and template
    provider_name = config.provider or kwargs.get("provider")
    template = config.image or get_recommended_template(provider_name) if provider_name else None

    # Build sandbox config
    sandbox_kwargs: dict[str, Any] = {
        "env_vars": config.get_env_vars(),
    }
    if provider_name:
        sandbox_kwargs["provider"] = provider_name
    if template:
        sandbox_kwargs["image"] = template
    if config.labels:
        sandbox_kwargs["labels"] = config.labels

    # Create sandbox
    sandbox = await Sandbox.create(**sandbox_kwargs)

    try:
        # Setup Claude Code
        await setup_claude_code(
            sandbox,
            provider_name=provider_name,
            anthropic_api_key=api_key,
        )

        # Change to working directory
        if config.working_dir:
            await sandbox.execute(f"cd {config.working_dir}")

        # Build command
        command = config.get_claude_command(prompt)

        # Stream output
        async for chunk in sandbox.stream(command):
            yield chunk

    finally:
        try:
            await sandbox.destroy()
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup sandbox: {cleanup_error}")
