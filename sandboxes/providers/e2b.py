"""E2B sandbox provider using the official E2B SDK."""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ..base import (
    ExecutionResult,
    ProviderCapabilities,
    Sandbox,
    SandboxConfig,
    SandboxProvider,
    SandboxState,
)
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError
from ..security import validate_download_path, validate_upload_path

logger = logging.getLogger(__name__)

try:
    from e2b import AsyncSandbox as E2BSandbox

    E2B_AVAILABLE = True
except ImportError:
    E2B_AVAILABLE = False
    E2BSandbox = None  # Define as None when not available
    logger.warning("E2B SDK not available - install with: uv add e2b")


class E2BProvider(SandboxProvider):
    """E2B sandbox provider using the official SDK."""

    CAPABILITIES = ProviderCapabilities(
        persistent=True,
        streaming=True,
        file_upload=True,
    )

    def __init__(self, api_key: str | None = None, **config):
        """
        Initialize E2B provider.

        Args:
            api_key: E2B API key. If not provided, reads from E2B_API_KEY environment variable.
            **config: Additional configuration options
        """
        super().__init__(**config)

        if not E2B_AVAILABLE:
            raise ProviderError("E2B SDK not installed")

        self.api_key = api_key or os.getenv("E2B_API_KEY")
        if not self.api_key:
            raise ProviderError("E2B API key not provided")

        # Configuration
        self.default_template = config.get("template", "base")  # E2B base template
        self.timeout = config.get("timeout", 60)

        # Track active sandboxes with metadata
        self._sandboxes: dict[str, dict[str, Any]] = {}

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Provider name."""
        return "e2b"

    async def _create_e2b_sandbox(self, template_id=None, env_vars=None, timeout=None):
        """Create E2B sandbox asynchronously."""
        # timeout sets the sandbox lifetime in seconds
        self._reset_e2b_transport_singleton()
        try:
            return await E2BSandbox.create(
                template=template_id,
                envs=env_vars,
                api_key=self.api_key,
                timeout=timeout or self.timeout,
            )
        except RuntimeError as e:
            # e2b>=2.13 uses a process-wide singleton async transport which can be bound
            # to a closed event loop across test loops; reset and retry once.
            if "Event loop is closed" not in str(e):
                raise
            self._reset_e2b_transport_singleton()
            return await E2BSandbox.create(
                template=template_id,
                envs=env_vars,
                api_key=self.api_key,
                timeout=timeout or self.timeout,
            )

    def _to_sandbox(self, e2b_sandbox, metadata: dict[str, Any]) -> Sandbox:
        """Convert E2B sandbox to standard Sandbox."""
        return Sandbox(
            id=e2b_sandbox.sandbox_id,
            provider=self.name,
            state=SandboxState.RUNNING,
            labels=metadata.get("labels", {}),
            created_at=metadata.get("created_at", datetime.now()),
            metadata={
                "timeout": self.timeout,
                "last_accessed": metadata.get("last_accessed", time.time()),
            },
        )

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new sandbox."""
        try:
            # Get template from config.image or provider_config, default to base
            template_id = (
                config.image
                or (config.provider_config.get("template") if config.provider_config else None)
                or self.default_template
            )

            # Create sandbox asynchronously with timeout for sandbox lifetime
            sandbox_timeout = config.timeout_seconds or self.timeout
            e2b_sandbox = await self._create_e2b_sandbox(
                template_id, config.env_vars, timeout=sandbox_timeout
            )

            # Store metadata
            metadata = {
                "e2b_sandbox": e2b_sandbox,
                "labels": config.labels or {},
                "created_at": datetime.now(),
                "last_accessed": time.time(),
                "config": config,
            }

            async with self._lock:
                self._sandboxes[e2b_sandbox.sandbox_id] = metadata

            logger.info(f"Created E2B sandbox {e2b_sandbox.sandbox_id}")

            # Run setup commands
            if config.setup_commands:
                for cmd in config.setup_commands:
                    await self.execute_command(e2b_sandbox.sandbox_id, cmd)

            return self._to_sandbox(e2b_sandbox, metadata)

        except Exception as e:
            logger.error(f"Failed to create E2B sandbox: {e}")
            raise SandboxError(f"Failed to create sandbox: {e}") from e

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID."""
        if sandbox_id in self._sandboxes:
            metadata = self._sandboxes[sandbox_id]
            metadata["last_accessed"] = time.time()
            return self._to_sandbox(metadata["e2b_sandbox"], metadata)
        return None

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List active sandboxes."""
        sandboxes = []

        # First, get all sandboxes from E2B API
        try:
            # E2B's list() can return either a coroutine or AsyncSandboxPaginator depending on version
            self._reset_e2b_transport_singleton()
            try:
                result = E2BSandbox.list(api_key=self.api_key)
            except TypeError:
                # Older SDK versions don't accept api_key in list()
                result = E2BSandbox.list()

            # Handle different return types
            if hasattr(result, "next_items"):
                # AsyncSandboxPaginator (e2b 2.x)
                e2b_listed = await result.next_items()
            else:
                # Coroutine (e2b 3.x)
                e2b_listed = await result

            for listed_sandbox in e2b_listed:
                # Check if we have it in local tracking
                if listed_sandbox.sandbox_id in self._sandboxes:
                    metadata = self._sandboxes[listed_sandbox.sandbox_id]
                else:
                    # Add untracked sandbox from API
                    metadata = {
                        "labels": listed_sandbox.metadata or {},
                        "created_at": listed_sandbox.started_at,
                        "last_accessed": time.time(),
                    }

                # Filter by labels if provided
                if labels:
                    sandbox_labels = metadata.get("labels", {})
                    if not all(sandbox_labels.get(k) == v for k, v in labels.items()):
                        continue

                # Convert ListedSandbox to our Sandbox format
                sandboxes.append(
                    Sandbox(
                        id=listed_sandbox.sandbox_id,
                        provider=self.name,
                        state=(
                            SandboxState.RUNNING
                            if listed_sandbox.state == "running"
                            else SandboxState.STOPPED
                        ),
                        labels=metadata.get("labels", {}),
                        created_at=listed_sandbox.started_at,
                        metadata={
                            "template": listed_sandbox.template_id,
                            "name": listed_sandbox.name,
                            "end_at": listed_sandbox.end_at,
                            "last_accessed": metadata.get("last_accessed"),
                        },
                    )
                )
        except Exception as e:
            logger.warning(f"Could not list E2B sandboxes from API: {e}")
            # Fallback to local tracking only
            for _sandbox_id, metadata in self._sandboxes.items():
                if labels:
                    sandbox_labels = metadata.get("labels", {})
                    if not all(sandbox_labels.get(k) == v for k, v in labels.items()):
                        continue
                sandboxes.append(self._to_sandbox(metadata["e2b_sandbox"], metadata))

        return sandboxes

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find a running sandbox with matching labels for reuse."""
        sandboxes = await self.list_sandboxes(labels=labels)
        if sandboxes:
            # Prefer tracked sandboxes so we can reuse recency metadata safely.
            tracked = [s for s in sandboxes if s.id in self._sandboxes]
            if tracked:
                tracked.sort(key=lambda s: self._sandboxes[s.id]["last_accessed"], reverse=True)
                logger.info(f"Found existing sandbox {tracked[0].id} with labels {labels}")
                return tracked[0]

            logger.info(f"Found API-listed sandbox {sandboxes[0].id} with labels {labels}")
            return sandboxes[0]
        return None

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute shell command in the sandbox."""
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            e2b_sandbox = metadata["e2b_sandbox"]
            metadata["last_accessed"] = time.time()

            start_time = time.time()
            effective_timeout = timeout or self.timeout

            # For long-running commands (>60s), use background execution with polling
            # to work around E2B SDK timeout issues
            if effective_timeout > 60:
                return await self._execute_long_running(
                    e2b_sandbox, command, effective_timeout, env_vars, start_time
                )

            # Execute command using AsyncSandbox.commands.run()
            # Pass envs directly to the run method
            try:
                result = await e2b_sandbox.commands.run(
                    command,
                    envs=env_vars,
                    timeout=effective_timeout,
                )
                exit_code = result.exit_code
                stdout = result.stdout
                stderr = result.stderr
            except Exception as e:
                # Handle CommandExitException - E2B raises this for non-zero exit codes
                if hasattr(e, "exit_code"):
                    exit_code = e.exit_code
                    stdout = getattr(e, "stdout", "")
                    stderr = getattr(e, "stderr", str(e))
                else:
                    raise

            duration_ms = int((time.time() - start_time) * 1000)

            # AsyncSandbox CommandResult has: stdout, stderr, exit_code, error
            return ExecutionResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                truncated=False,
                timed_out=False,
            )

        except Exception as e:
            logger.error(f"Failed to execute command in sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to execute command: {e}") from e

    async def _execute_long_running(
        self,
        e2b_sandbox,
        command: str,
        timeout: int,
        env_vars: dict[str, str] | None,
        start_time: float,
    ) -> ExecutionResult:
        """Execute long-running command using background execution with polling.

        This works around E2B SDK timeout issues by running the command in background
        and polling for completion.
        """
        import uuid

        # Create unique output files
        run_id = uuid.uuid4().hex[:8]
        stdout_file = f"/tmp/cmd_{run_id}_stdout.txt"
        stderr_file = f"/tmp/cmd_{run_id}_stderr.txt"
        exit_file = f"/tmp/cmd_{run_id}_exit.txt"

        # Build wrapper command that captures output and exit code
        # Use nohup and & for background execution
        # Escape single quotes in command for shell
        escaped_command = command.replace("'", "'\"'\"'")
        wrapper = f"""
nohup sh -c '{escaped_command} > {stdout_file} 2> {stderr_file}; echo $? > {exit_file}' > /dev/null 2>&1 &
echo $!
"""
        # Start the command in background
        try:
            result = await e2b_sandbox.commands.run(wrapper, envs=env_vars, timeout=10)
            pid = result.stdout.strip()
        except Exception as e:
            logger.error(f"Failed to start background command: {e}")
            raise

        # Poll for completion
        poll_interval = 1.0  # seconds
        deadline = time.time() + timeout

        while time.time() < deadline:
            await asyncio.sleep(poll_interval)

            # Check if exit code file exists (command completed)
            try:
                check_result = await e2b_sandbox.commands.run(
                    f"cat {exit_file} 2>/dev/null || echo ''", timeout=5
                )
                exit_code_str = check_result.stdout.strip()
                if exit_code_str:
                    # Command completed
                    exit_code = int(exit_code_str)

                    # Read stdout
                    stdout_result = await e2b_sandbox.commands.run(
                        f"cat {stdout_file} 2>/dev/null || echo ''", timeout=10
                    )
                    stdout = stdout_result.stdout

                    # Read stderr
                    stderr_result = await e2b_sandbox.commands.run(
                        f"cat {stderr_file} 2>/dev/null || echo ''", timeout=10
                    )
                    stderr = stderr_result.stdout

                    # Cleanup temp files
                    await e2b_sandbox.commands.run(
                        f"rm -f {stdout_file} {stderr_file} {exit_file}", timeout=5
                    )

                    duration_ms = int((time.time() - start_time) * 1000)
                    return ExecutionResult(
                        exit_code=exit_code,
                        stdout=stdout,
                        stderr=stderr,
                        duration_ms=duration_ms,
                        truncated=False,
                        timed_out=False,
                    )
            except Exception as poll_error:
                logger.warning(f"Poll error: {poll_error}")
                continue

        # Timeout - try to kill the process
        try:
            await e2b_sandbox.commands.run(f"kill {pid} 2>/dev/null || true", timeout=5)
        except Exception as e:
            logger.debug(f"Failed to kill timed-out process {pid}: {e}")

        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout} seconds",
            duration_ms=duration_ms,
            truncated=False,
            timed_out=True,
        )

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream execution output (simulated for E2B)."""
        # E2B SDK doesn't support streaming, so we execute and yield chunks
        result = await self.execute_command(sandbox_id, command, timeout, env_vars)

        # Yield output in chunks to simulate streaming
        chunk_size = 256
        output = result.stdout

        for i in range(0, len(output), chunk_size):
            yield output[i : i + chunk_size]
            await asyncio.sleep(0.01)  # Small delay to simulate streaming

        if result.stderr:
            yield f"\n[Error]: {result.stderr}"

    async def upload_file(self, sandbox_id: str, local_path: str, remote_path: str) -> bool:
        """Upload a file to the sandbox."""
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            # Validate local path to prevent path traversal attacks
            validated_path = validate_upload_path(local_path)

            metadata = self._sandboxes[sandbox_id]
            e2b_sandbox = metadata["e2b_sandbox"]

            # Read local file content from validated path
            with open(validated_path, "rb") as f:
                content = f.read()

            # Write to sandbox filesystem (remote_path is inside sandbox, so it's safe)
            await e2b_sandbox.files.write(remote_path, content)

            logger.info(f"Uploaded {validated_path} to {remote_path} in sandbox {sandbox_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to upload file to sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to upload file: {e}") from e

    async def download_file(self, sandbox_id: str, remote_path: str, local_path: str) -> bool:
        """Download a file from the sandbox."""
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            # Validate local path to prevent path traversal attacks
            validated_path = validate_download_path(local_path)

            metadata = self._sandboxes[sandbox_id]
            e2b_sandbox = metadata["e2b_sandbox"]

            # Read from sandbox filesystem (remote_path is inside sandbox, so it's safe)
            content = await e2b_sandbox.files.read(remote_path)

            # Write to local file at validated path
            with open(validated_path, "wb") as f:
                # Handle both bytes and str
                if isinstance(content, str):
                    f.write(content.encode())
                else:
                    f.write(content)

            logger.info(f"Downloaded {remote_path} from sandbox {sandbox_id} to {validated_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to download file from sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to download file: {e}") from e

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox."""
        try:
            # Check if we have it in local tracking
            if sandbox_id in self._sandboxes:
                metadata = self._sandboxes[sandbox_id]
                e2b_sandbox = metadata["e2b_sandbox"]
            else:
                # Try to connect to it via API
                self._reset_e2b_transport_singleton()
                e2b_sandbox = await E2BSandbox.connect(sandbox_id)

            # Kill sandbox asynchronously
            await e2b_sandbox.kill()

            # Remove from tracking if present
            if sandbox_id in self._sandboxes:
                async with self._lock:
                    del self._sandboxes[sandbox_id]

            logger.info(f"Destroyed E2B sandbox {sandbox_id}")
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
        """Check if E2B service is accessible."""
        try:
            # Try to create and destroy a test sandbox
            config = SandboxConfig()
            sandbox = await self.create_sandbox(config)
            result = await self.execute_command(sandbox.id, "echo 'health check'")
            await self.destroy_sandbox(sandbox.id)
            return result.success
        except Exception as e:
            logger.error(f"E2B health check failed: {e}")
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

    def __del__(self):
        """Cleanup on deletion."""
        # Shutdown thread pool
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=False)

    @staticmethod
    def _reset_e2b_transport_singleton() -> None:
        """Reset e2b async transport singleton to avoid closed-loop reuse across test loops."""
        try:
            from e2b.api import client_async as e2b_client_async

            transport_cls = getattr(e2b_client_async, "AsyncTransportWithLogger", None)
            if transport_cls is not None:
                transport_cls.singleton = None
        except Exception:
            # Best-effort reset for compatibility across SDK versions.
            pass
