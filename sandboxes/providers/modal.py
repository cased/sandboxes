"""Modal sandbox provider implementation."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError

logger = logging.getLogger(__name__)

try:
    import modal
    from modal import Sandbox as ModalSandbox

    MODAL_AVAILABLE = True
except ImportError:
    MODAL_AVAILABLE = False
    ModalSandbox = None  # Define as None when not available
    modal = None
    logger.warning("Modal SDK not available - install with: pip install modal")


class ModalProvider(SandboxProvider):
    """Modal sandbox provider implementation."""

    def __init__(self, **config):
        """Initialize Modal provider.

        Modal uses token authentication configured in ~/.modal.toml
        """
        super().__init__(**config)

        if not MODAL_AVAILABLE:
            raise ProviderError("Modal SDK not installed")

        # Modal automatically uses tokens from ~/.modal.toml
        # No explicit client initialization needed - Modal SDK handles this

        # Configuration
        # Use Daytona's AI-optimized image for fair comparison
        # Includes Python 3.13, numpy, requests, and many AI/ML packages
        self.default_image = config.get("image", "daytonaio/ai-test:0.2.3")
        self.default_cpu = config.get("cpu", 2.0)
        self.default_memory = config.get("memory", 2048)
        self.timeout = config.get("timeout", 300)
        # Track active sandboxes with metadata
        self._sandboxes: dict[str, dict[str, Any]] = {}

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Provider name."""
        return "modal"

    def _to_sandbox(self, modal_sandbox: ModalSandbox, metadata: dict[str, Any]) -> Sandbox:
        """Convert Modal sandbox to standard Sandbox."""
        return Sandbox(
            id=modal_sandbox.object_id,
            provider=self.name,
            state=SandboxState.RUNNING,  # Modal sandboxes are always running until terminated
            labels=metadata.get("labels", {}),
            created_at=metadata.get("created_at", datetime.now()),
            metadata={
                "image": metadata.get("image", self.default_image),
                "cpu": metadata.get("cpu", self.default_cpu),
                "memory": metadata.get("memory", self.default_memory),
                "last_accessed": metadata.get("last_accessed", time.time()),
            },
        )

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new sandbox."""
        try:
            # Extract Modal-specific config
            # Prefer config.image, then provider_config["image"], then default
            image = (
                config.image
                or (config.provider_config.get("image") if config.provider_config else None)
                or self.default_image
            )
            # Prioritize standard config fields, then provider_config, then defaults
            cpu = (
                config.cpu_cores
                or (config.provider_config.get("cpu") if config.provider_config else None)
                or self.default_cpu
            )
            memory = (
                config.memory_mb
                or (config.provider_config.get("memory") if config.provider_config else None)
                or self.default_memory
            )
            timeout = config.timeout_seconds or self.timeout

            app = await modal.App.lookup.aio("sandboxes-provider", create_if_missing=True)
            modal_image = modal.Image.from_registry(image) if isinstance(image, str) else image
            modal_sandbox = await ModalSandbox.create.aio(
                app=app,
                image=modal_image,
                cpu=cpu,
                memory=memory,
                timeout=timeout,
            )

            # Store metadata - include env_vars for use in each command
            metadata = {
                "modal_sandbox": modal_sandbox,
                "labels": config.labels or {},
                "created_at": datetime.now(),
                "last_accessed": time.time(),
                "config": config,
                "image": image,
                "cpu": cpu,
                "memory": memory,
                "env_vars": config.env_vars or {},  # Store for each command
            }

            async with self._lock:
                self._sandboxes[modal_sandbox.object_id] = metadata

            logger.info(f"Created Modal sandbox {modal_sandbox.object_id}")

            # Run setup commands
            if config.setup_commands:
                for cmd in config.setup_commands:
                    await self.execute_command(modal_sandbox.object_id, cmd)

            return self._to_sandbox(modal_sandbox, metadata)

        except Exception as e:
            logger.error(f"Failed to create Modal sandbox: {e}")
            raise SandboxError(f"Failed to create sandbox: {e}") from e

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID."""
        if sandbox_id in self._sandboxes:
            metadata = self._sandboxes[sandbox_id]
            metadata["last_accessed"] = time.time()
            return self._to_sandbox(metadata["modal_sandbox"], metadata)

        # Try to fetch from Modal API
        try:
            modal_sandbox = await ModalSandbox.from_id.aio(sandbox_id)

            # Create metadata for found sandbox
            metadata = {
                "modal_sandbox": modal_sandbox,
                "labels": {},
                "created_at": datetime.now(),
                "last_accessed": time.time(),
                "image": self.default_image,
                "cpu": self.default_cpu,
                "memory": self.default_memory,
            }

            async with self._lock:
                self._sandboxes[sandbox_id] = metadata

            return self._to_sandbox(modal_sandbox, metadata)
        except Exception:
            return None

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List active sandboxes."""
        sandboxes = []

        # First check our tracked sandboxes
        for _sandbox_id, metadata in self._sandboxes.items():
            # Filter by labels if provided
            if labels:
                sandbox_labels = metadata.get("labels", {})
                if not all(sandbox_labels.get(k) == v for k, v in labels.items()):
                    continue

            sandboxes.append(self._to_sandbox(metadata["modal_sandbox"], metadata))

        # Also try to list from Modal API
        try:
            modal_sandboxes = [s async for s in ModalSandbox.list.aio()]

            for modal_sandbox in modal_sandboxes:
                if modal_sandbox.object_id not in self._sandboxes:
                    # Add untracked sandboxes
                    metadata = {
                        "modal_sandbox": modal_sandbox,
                        "labels": {},
                        "created_at": datetime.now(),
                        "last_accessed": time.time(),
                        "image": self.default_image,
                        "cpu": self.default_cpu,
                        "memory": self.default_memory,
                    }

                    if not labels:  # Only add if no label filter or we can't check
                        sandboxes.append(self._to_sandbox(modal_sandbox, metadata))
        except Exception as e:
            logger.warning(f"Could not list Modal sandboxes from API: {e}")

        return sandboxes

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find a running sandbox with matching labels for reuse."""
        sandboxes = await self.list_sandboxes(labels=labels)
        if sandboxes:
            # Return most recently accessed
            sandboxes.sort(
                key=lambda s: self._sandboxes.get(s.id, {}).get("last_accessed", 0), reverse=True
            )
            logger.info(f"Found existing sandbox {sandboxes[0].id} with labels {labels}")
            return sandboxes[0]
        return None

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute command in the sandbox."""
        if sandbox_id not in self._sandboxes:
            # Try to fetch from API
            sandbox = await self.get_sandbox(sandbox_id)
            if not sandbox:
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            modal_sandbox = metadata["modal_sandbox"]
            metadata["last_accessed"] = time.time()

            # Combine stored env_vars with any passed env_vars
            all_env_vars = dict(metadata.get("env_vars", {}))
            if env_vars:
                all_env_vars.update(env_vars)

            # Prepare command with environment variables (with proper escaping)
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

                env_setup = " && ".join(
                    [
                        f"export {validate_env_key(k)}='{escape_shell_value(str(v))}'"
                        for k, v in all_env_vars.items()
                    ]
                )
                command = f"{env_setup} && {command}"

            start_time = time.time()

            # Modal's exec returns a process object
            # Use 'sh' instead of 'bash' for alpine compatibility.
            process = await modal_sandbox.exec.aio("sh", "-c", command, timeout=timeout or self.timeout)

            # Wait for completion before reading process output.
            exit_code = await process.wait.aio()
            stdout = await process.stdout.read.aio() if process.stdout else ""
            stderr = await process.stderr.read.aio() if process.stderr else ""

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecutionResult(
                exit_code=exit_code or 0,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                truncated=False,
                timed_out=False,
            )

        except Exception as e:
            logger.error(f"Failed to execute command in sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to execute command: {e}") from e

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream execution output."""
        # Modal doesn't support streaming directly, so we execute and yield chunks
        result = await self.execute_command(sandbox_id, command, timeout, env_vars)

        # Yield output in chunks to simulate streaming
        chunk_size = 256
        output = result.stdout

        for i in range(0, len(output), chunk_size):
            yield output[i : i + chunk_size]
            await asyncio.sleep(0.01)  # Small delay to simulate streaming

        if result.stderr:
            yield f"\n[Error]: {result.stderr}"

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox."""
        if sandbox_id not in self._sandboxes:
            # Try to fetch from API
            try:
                modal_sandbox = await ModalSandbox.from_id.aio(sandbox_id)
                await modal_sandbox.terminate.aio()
                return True
            except Exception:
                return False

        try:
            metadata = self._sandboxes[sandbox_id]
            modal_sandbox = metadata["modal_sandbox"]

            await modal_sandbox.terminate.aio()

            # Remove from tracking
            async with self._lock:
                del self._sandboxes[sandbox_id]

            logger.info(f"Destroyed Modal sandbox {sandbox_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to destroy sandbox {sandbox_id}: {e}")
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
        """Check if Modal service is accessible."""
        try:
            # Try to create and destroy a test sandbox
            config = SandboxConfig()
            sandbox = await self.create_sandbox(config)
            result = await self.execute_command(sandbox.id, "echo 'health check'")
            await self.destroy_sandbox(sandbox.id)
            return result.success
        except Exception as e:
            logger.error(f"Modal health check failed: {e}")
            return False

    async def cleanup_idle_sandboxes(self, idle_timeout: int = 600):
        """Clean up sandboxes that have been idle."""
        current_time = time.time()
        to_destroy = []

        for sandbox_id, metadata in self._sandboxes.items():
            last_accessed = metadata.get("last_accessed", current_time)
            if current_time - last_accessed > idle_timeout:
                to_destroy.append(sandbox_id)

        for sandbox_id in to_destroy:
            logger.info(f"Cleaning up idle sandbox {sandbox_id}")
            await self.destroy_sandbox(sandbox_id)
