"""Fly.io Sprites sandbox provider implementation."""

import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError

logger = logging.getLogger(__name__)

try:
    from sprites import SpritesClient

    SPRITES_SDK_AVAILABLE = True
except ImportError:
    SPRITES_SDK_AVAILABLE = False
    SpritesClient = None

# Check if sprite CLI is available
SPRITES_CLI_AVAILABLE = shutil.which("sprite") is not None

SPRITES_AVAILABLE = SPRITES_SDK_AVAILABLE or SPRITES_CLI_AVAILABLE

if not SPRITES_AVAILABLE:
    logger.warning(
        "Sprites not available - install SDK with: pip install sprites-py "
        "or CLI with: curl https://sprites.dev/install.sh | bash"
    )


class SpritesProvider(SandboxProvider):
    """Fly.io Sprites sandbox provider implementation.

    Sprites are persistent, hardware-isolated Linux sandboxes with:
    - Fast startup (1-2 seconds)
    - 100GB storage
    - Checkpoint/restore support
    - Automatic idle suspension
    - Claude Code, Node.js 22, Python 3.13 pre-installed

    Supports two modes:
    - SDK mode: Uses sprites-py with SPRITES_TOKEN
    - CLI mode: Uses sprite CLI with existing login (sprite login)
    """

    def __init__(self, token: str | None = None, use_cli: bool = False, **config):
        """Initialize Sprites provider.

        Args:
            token: Sprites API token. If not provided, reads from SPRITES_TOKEN env var.
            use_cli: Force using CLI instead of SDK (useful if logged in via sprite login)
            **config: Additional configuration options
        """
        super().__init__(**config)

        self.token = token or os.getenv("SPRITES_TOKEN")
        self.use_cli = use_cli or not self.token

        if self.use_cli:
            if not SPRITES_CLI_AVAILABLE:
                raise ProviderError(
                    "Sprites CLI not found. Install with: curl https://sprites.dev/install.sh | bash"
                )
            self.client = None
            logger.info("Using Sprites CLI mode (sprite command)")
        else:
            if not SPRITES_SDK_AVAILABLE:
                raise ProviderError(
                    "Sprites SDK not installed. Install with: pip install sprites-py"
                )
            self.client = SpritesClient(token=self.token)
            logger.info("Using Sprites SDK mode")

        # Default timeout for command execution
        self.default_timeout = config.get("timeout", 300)

        # Track sandbox metadata including env_vars
        self._sandbox_metadata: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        """Provider name."""
        return "sprites"

    def _generate_sprite_name(self) -> str:
        """Generate a unique sprite name."""
        return f"sandbox-{uuid.uuid4().hex[:12]}"

    def _to_sandbox(self, sprite_name: str, metadata: dict[str, Any]) -> Sandbox:
        """Convert sprite to standard Sandbox."""
        return Sandbox(
            id=sprite_name,
            provider=self.name,
            state=SandboxState.RUNNING,  # Sprites are always running or don't exist
            labels=metadata.get("labels", {}),
            created_at=metadata.get("created_at", datetime.now()),
            metadata={
                "last_accessed": metadata.get("last_accessed", time.time()),
            },
        )

    async def _run_cli(self, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        """Run sprite CLI command."""
        cmd = ["sprite", *args]
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
            ),
        )

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new sprite sandbox."""
        try:
            # Generate unique name or use provided one
            sprite_name = (
                config.provider_config.get("name") if config.provider_config else None
            ) or self._generate_sprite_name()

            logger.info(f"Creating Sprites sandbox: {sprite_name}")

            if self.use_cli:
                # Use CLI: sprite create <name>
                result = await self._run_cli("create", sprite_name)
                if result.returncode != 0:
                    raise SandboxError(f"Failed to create sprite: {result.stderr}")
            else:
                # Use SDK
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.client.create_sprite, sprite_name)

            # Store metadata including env_vars
            metadata = {
                "labels": config.labels or {},
                "created_at": datetime.now(),
                "last_accessed": time.time(),
                "env_vars": config.env_vars or {},
            }
            self._sandbox_metadata[sprite_name] = metadata

            logger.info(f"Created Sprites sandbox {sprite_name}")

            sandbox = self._to_sandbox(sprite_name, metadata)

            # Run setup commands if provided
            if config.setup_commands:
                for cmd in config.setup_commands:
                    await self.execute_command(sprite_name, cmd)

            return sandbox

        except Exception as e:
            logger.error(f"Failed to create Sprites sandbox: {e}")
            raise SandboxError(f"Failed to create sandbox: {e}") from e

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID (sprite name)."""
        if sandbox_id in self._sandbox_metadata:
            metadata = self._sandbox_metadata[sandbox_id]
            metadata["last_accessed"] = time.time()
            return self._to_sandbox(sandbox_id, metadata)

        # Try to access the sprite to check if it exists
        try:
            sprite = self.client.sprite(sandbox_id)
            # Run a quick command to verify it's accessible
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: sprite.run("true", capture_output=True, timeout=10)
            )

            # Create metadata for found sprite
            metadata = {
                "labels": {},
                "created_at": datetime.now(),
                "last_accessed": time.time(),
                "env_vars": {},
            }
            self._sandbox_metadata[sandbox_id] = metadata
            return self._to_sandbox(sandbox_id, metadata)
        except Exception:
            return None

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List tracked sandboxes."""
        # Sprites SDK doesn't have a list method, so we use local tracking
        sandboxes = []

        for sprite_name, metadata in self._sandbox_metadata.items():
            # Filter by labels if provided
            if labels:
                sandbox_labels = metadata.get("labels", {})
                if not all(sandbox_labels.get(k) == v for k, v in labels.items()):
                    continue
            sandboxes.append(self._to_sandbox(sprite_name, metadata))

        return sandboxes

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find a running sandbox with matching labels for reuse."""
        sandboxes = await self.list_sandboxes(labels=labels)
        if sandboxes:
            # Return most recently accessed
            sandboxes.sort(
                key=lambda s: self._sandbox_metadata.get(s.id, {}).get("last_accessed", 0),
                reverse=True,
            )
            logger.info(f"Found existing sprite {sandboxes[0].id} with labels {labels}")
            return sandboxes[0]
        return None

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute command in the sprite."""
        try:
            # Update last accessed time
            if sandbox_id in self._sandbox_metadata:
                self._sandbox_metadata[sandbox_id]["last_accessed"] = time.time()

            # Combine stored env_vars with any passed env_vars
            all_env_vars = dict(self._sandbox_metadata.get(sandbox_id, {}).get("env_vars", {}))
            if env_vars:
                all_env_vars.update(env_vars)

            # Prepare command with environment variables
            # Escape single quotes in values to prevent shell injection
            if all_env_vars:
                import re

                def escape_shell_value(val: str) -> str:
                    """Escape single quotes for shell: ' -> '\\''"""
                    return val.replace("'", "'\\''")

                def validate_env_key(key: str) -> str:
                    """Validate env var key contains only safe characters."""
                    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                        raise ValueError(f"Invalid environment variable name: {key}")
                    return key

                exports = " && ".join(
                    [
                        f"export {validate_env_key(k)}='{escape_shell_value(str(v))}'"
                        for k, v in all_env_vars.items()
                    ]
                )
                command = f"{exports} && {command}"

            start_time = time.time()

            if self.use_cli:
                # Use CLI: sprite exec -s <name> -- sh -c "<command>"
                result = await self._run_cli(
                    "exec",
                    "-s",
                    sandbox_id,
                    "--",
                    "sh",
                    "-c",
                    command,
                    timeout=timeout or self.default_timeout,
                )
                stdout = result.stdout
                stderr = result.stderr
                returncode = result.returncode
            else:
                # Use SDK
                sprite = self.client.sprite(sandbox_id)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: sprite.run(
                        "sh",
                        "-c",
                        command,
                        capture_output=True,
                        timeout=timeout or self.default_timeout,
                    ),
                )
                stdout = (
                    result.stdout.decode()
                    if isinstance(result.stdout, bytes)
                    else (result.stdout or "")
                )
                stderr = (
                    result.stderr.decode()
                    if isinstance(result.stderr, bytes)
                    else (result.stderr or "")
                )
                returncode = result.returncode

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecutionResult(
                exit_code=returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                truncated=False,
                timed_out=False,
            )

        except Exception as e:
            error_str = str(e).lower()
            if "not found" in error_str or "does not exist" in error_str:
                raise SandboxNotFoundError(f"Sprite {sandbox_id} not found") from e
            logger.error(f"Failed to execute command in sprite {sandbox_id}: {e}")
            raise SandboxError(f"Failed to execute command: {e}") from e

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream execution output."""
        # Sprites SDK doesn't support streaming directly, so we execute and yield chunks
        result = await self.execute_command(sandbox_id, command, timeout, env_vars)

        # Yield output in chunks to simulate streaming
        chunk_size = 256
        output = result.stdout

        for i in range(0, len(output), chunk_size):
            yield output[i : i + chunk_size]
            await asyncio.sleep(0.01)

        if result.stderr:
            yield f"\n[Error]: {result.stderr}"

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sprite."""
        try:
            if self.use_cli:
                # Use CLI: sprite destroy -s <name> -force
                result = await self._run_cli("destroy", "-s", sandbox_id, "-force")
                if result.returncode != 0:
                    combined = result.stdout + result.stderr
                    if "not found" not in combined.lower():
                        raise SandboxError(f"Failed to delete sprite: {combined}")
            else:
                # Use SDK
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.client.delete_sprite, sandbox_id)

            # Remove from tracking
            if sandbox_id in self._sandbox_metadata:
                del self._sandbox_metadata[sandbox_id]

            logger.info(f"Destroyed Sprites sandbox {sandbox_id}")
            return True

        except Exception as e:
            error_str = str(e).lower()
            if "not found" in error_str or "does not exist" in error_str:
                # Already deleted
                if sandbox_id in self._sandbox_metadata:
                    del self._sandbox_metadata[sandbox_id]
                return True
            logger.error(f"Failed to destroy sprite {sandbox_id}: {e}")
            raise SandboxError(f"Failed to destroy sandbox: {e}") from e

    async def execute_commands(
        self,
        sandbox_id: str,
        commands: list[str],
        stop_on_error: bool = True,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> list[ExecutionResult]:
        """Execute multiple commands in sequence."""
        results = []

        for command in commands:
            result = await self.execute_command(sandbox_id, command, timeout, env_vars)
            results.append(result)

            if stop_on_error and not result.success:
                logger.warning(f"Command failed, stopping sequence: {command}")
                break

        return results

    async def get_or_create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Get existing sandbox with matching labels or create new one."""
        # Try to find existing sandbox if labels provided
        if config.labels:
            existing = await self.find_sandbox(config.labels)
            if existing:
                return existing

        # Create new sandbox
        return await self.create_sandbox(config)

    async def health_check(self) -> bool:
        """Check if Sprites service is accessible."""
        try:
            config = SandboxConfig()
            sandbox = await self.create_sandbox(config)
            result = await self.execute_command(sandbox.id, "echo 'health check'")
            await self.destroy_sandbox(sandbox.id)
            return result.success
        except Exception as e:
            logger.error(f"Sprites health check failed: {e}")
            return False

    async def create_checkpoint(self, sandbox_id: str, name: str | None = None) -> str:
        """Create a checkpoint of the sprite state.

        Args:
            sandbox_id: The sprite name
            name: Optional checkpoint name/description

        Returns:
            Checkpoint ID
        """
        try:
            sprite = self.client.sprite(sandbox_id)
            loop = asyncio.get_event_loop()

            # Create checkpoint - returns a stream of messages
            checkpoint_id = None
            stream = await loop.run_in_executor(
                None, lambda: sprite.create_checkpoint(name or "checkpoint")
            )
            for msg in stream:
                if hasattr(msg, "checkpoint_id"):
                    checkpoint_id = msg.checkpoint_id

            logger.info(f"Created checkpoint {checkpoint_id} for sprite {sandbox_id}")
            return checkpoint_id

        except Exception as e:
            logger.error(f"Failed to create checkpoint for sprite {sandbox_id}: {e}")
            raise SandboxError(f"Failed to create checkpoint: {e}") from e

    async def restore_checkpoint(self, sandbox_id: str, checkpoint_id: str) -> bool:
        """Restore a sprite to a checkpoint.

        Args:
            sandbox_id: The sprite name
            checkpoint_id: The checkpoint ID to restore

        Returns:
            True if successful
        """
        try:
            sprite = self.client.sprite(sandbox_id)
            loop = asyncio.get_event_loop()

            # Restore checkpoint
            await loop.run_in_executor(None, lambda: list(sprite.restore_checkpoint(checkpoint_id)))

            logger.info(f"Restored sprite {sandbox_id} to checkpoint {checkpoint_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore checkpoint for sprite {sandbox_id}: {e}")
            raise SandboxError(f"Failed to restore checkpoint: {e}") from e

    async def create_claude_code_checkpoint(self, sandbox_id: str) -> str:
        """Create a checkpoint with Claude Code pre-installed.

        This is useful for creating reusable Sprites with Claude Code ready to go.
        After calling this, you can restore from the checkpoint for instant starts.

        Args:
            sandbox_id: The sprite name

        Returns:
            Checkpoint ID that can be used with restore_checkpoint()

        Example:
            # One-time setup
            provider = SpritesProvider(token="...")
            sandbox = await provider.create_sandbox(SandboxConfig())

            # Install Node.js and Claude Code
            await provider.execute_command(sandbox.id,
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -")
            await provider.execute_command(sandbox.id, "apt-get install -y nodejs")
            await provider.execute_command(sandbox.id,
                "npm install -g @anthropic-ai/claude-code")

            # Checkpoint with Claude Code installed
            checkpoint_id = await provider.create_claude_code_checkpoint(sandbox.id)
            print(f"Claude Code checkpoint: {checkpoint_id}")

            # Later: instant restore with Claude Code ready
            await provider.restore_checkpoint(sandbox.id, checkpoint_id)
            # Claude Code is immediately available!
        """
        # Verify Claude Code is installed before checkpointing
        result = await self.execute_command(sandbox_id, "claude --version")
        if not result.success:
            raise SandboxError(
                "Claude Code not installed. Install it first with: "
                "npm install -g @anthropic-ai/claude-code"
            )

        return await self.create_checkpoint(sandbox_id, "claude-code-ready")
