"""Hopx sandbox provider using the official hopx-ai SDK."""

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError
from ..security import validate_download_path, validate_upload_path

logger = logging.getLogger(__name__)

try:
    from hopx_ai import AsyncSandbox as HopxSandbox

    HOPX_AVAILABLE = True
except ImportError:
    HOPX_AVAILABLE = False
    HopxSandbox = None
    logger.warning("Hopx SDK not available - install with: pip install hopx-ai")


class HopxProvider(SandboxProvider):
    """Hopx sandbox provider using the official hopx-ai SDK."""

    def __init__(self, api_key: str | None = None, **config):
        """
        Initialize Hopx provider.

        Args:
            api_key: Hopx API key. If not provided, reads from HOPX_API_KEY environment variable.
            **config: Additional configuration options
        """
        super().__init__(**config)

        if not HOPX_AVAILABLE:
            raise ProviderError("Hopx SDK not installed")

        self.api_key = api_key or os.getenv("HOPX_API_KEY")
        if not self.api_key:
            raise ProviderError("Hopx API key not provided")

        # Configuration
        self.default_template = config.get("template", "code-interpreter")
        self.timeout = config.get("timeout", 300)
        self.base_url = config.get("base_url", "https://api.hopx.dev")

        # Track active sandboxes with metadata (like E2B pattern)
        self._sandboxes: dict[str, dict[str, Any]] = {}

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Provider name."""
        return "hopx"

    def _to_sandbox(self, hopx_sandbox, metadata: dict[str, Any]) -> Sandbox:
        """Convert Hopx SDK sandbox to standard Sandbox."""
        # Map Hopx status to SandboxState
        # Hopx API statuses: running, stopped, paused, creating (verified from SDK models.py:221)
        status = metadata.get("status", "running").lower()
        state_mapping = {
            "running": SandboxState.RUNNING,
            "stopped": SandboxState.STOPPED,
            "paused": SandboxState.STOPPED,  # Hopx paused maps to STOPPED
            "creating": SandboxState.CREATING,
        }
        state = state_mapping.get(status, SandboxState.RUNNING)

        public_host = metadata.get("public_host", "")

        return Sandbox(
            id=hopx_sandbox.sandbox_id,
            provider=self.name,
            state=state,
            labels=metadata.get("labels", {}),
            created_at=metadata.get("created_at", datetime.now()),
            connection_info={
                "public_host": public_host,
                "agent_url": f"{public_host}/" if public_host else "",
            },
            metadata={
                "template": metadata.get("template", self.default_template),
                "last_accessed": metadata.get("last_accessed", time.time()),
                "public_host": public_host,
            },
        )

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new sandbox using Hopx SDK."""
        try:
            # Get template from config.image or provider_config, default to code-interpreter
            template = (
                config.image
                or (config.provider_config.get("template") if config.provider_config else None)
                or self.default_template
            )

            # Get timeout configuration
            timeout_seconds = config.timeout_seconds or self.timeout

            # Create sandbox using SDK
            hopx_sandbox = await HopxSandbox.create(
                template=template,
                env_vars=config.env_vars,
                timeout_seconds=timeout_seconds,
                api_key=self.api_key,
                base_url=self.base_url,
            )

            # Get sandbox info to retrieve public host
            info = await hopx_sandbox.get_info()

            # Store metadata locally (following E2B pattern)
            metadata = {
                "hopx_sandbox": hopx_sandbox,
                "labels": config.labels or {},
                "created_at": datetime.now(),
                "last_accessed": time.time(),
                "template": template,
                "public_host": info.public_host,
                "status": info.status,
                "config": config,
            }

            async with self._lock:
                self._sandboxes[hopx_sandbox.sandbox_id] = metadata

            logger.info(f"Created Hopx sandbox {hopx_sandbox.sandbox_id} with template {template}")

            # Run setup commands if provided
            if config.setup_commands:
                for cmd in config.setup_commands:
                    await self.execute_command(hopx_sandbox.sandbox_id, cmd)

            return self._to_sandbox(hopx_sandbox, metadata)

        except Exception as e:
            logger.error(f"Failed to create Hopx sandbox: {e}")
            raise SandboxError(f"Failed to create sandbox: {e}") from e

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID."""
        if sandbox_id in self._sandboxes:
            metadata = self._sandboxes[sandbox_id]
            metadata["last_accessed"] = time.time()
            return self._to_sandbox(metadata["hopx_sandbox"], metadata)
        return None

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List active sandboxes, optionally filtered by labels."""
        sandboxes = []

        # Try to get sandboxes from Hopx API
        try:
            # Use SDK to list sandboxes
            hopx_sandboxes = await HopxSandbox.list(api_key=self.api_key, base_url=self.base_url)

            for hopx_sandbox in hopx_sandboxes:
                # Check if we have it in local tracking
                if hopx_sandbox.sandbox_id in self._sandboxes:
                    metadata = self._sandboxes[hopx_sandbox.sandbox_id]
                else:
                    # Add untracked sandbox from API
                    info = await hopx_sandbox.get_info()
                    metadata = {
                        "hopx_sandbox": hopx_sandbox,
                        "labels": {},
                        "created_at": info.created_at or datetime.now(),
                        "last_accessed": time.time(),
                        "template": info.template_name or self.default_template,
                        "public_host": info.public_host,
                        "status": info.status,
                    }

                # Filter by labels if provided
                if labels:
                    sandbox_labels = metadata.get("labels", {})
                    if not all(sandbox_labels.get(k) == v for k, v in labels.items()):
                        continue

                sandboxes.append(self._to_sandbox(hopx_sandbox, metadata))

        except Exception as e:
            logger.warning(f"Could not list Hopx sandboxes from API: {e}")
            # Fallback to local tracking only
            for _sandbox_id, metadata in self._sandboxes.items():
                if labels:
                    sandbox_labels = metadata.get("labels", {})
                    if not all(sandbox_labels.get(k) == v for k, v in labels.items()):
                        continue
                sandboxes.append(self._to_sandbox(metadata["hopx_sandbox"], metadata))

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
        """Execute shell command in the sandbox."""
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]
            metadata["last_accessed"] = time.time()

            start_time = time.time()

            # Execute command using SDK
            result = await hopx_sandbox.commands.run(
                command=command,
                timeout_seconds=timeout or self.timeout,
                env=env_vars,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecutionResult(
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                truncated=False,
                timed_out=False,
            )

        except Exception as e:
            logger.error(f"Failed to execute command in sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to execute command: {e}") from e

    async def run_code(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Execute code with rich output capture (plots, DataFrames, etc.).

        This method captures rich outputs like matplotlib plots, pandas DataFrames,
        and other visualizations automatically.

        Args:
            sandbox_id: Sandbox ID
            code: Code to execute
            language: Language (python, javascript, bash, go)
            timeout: Execution timeout in seconds
            env_vars: Optional environment variables

        Returns:
            Dictionary with:
                - success: bool
                - stdout: str
                - stderr: str
                - exit_code: int
                - execution_time: float
                - rich_outputs: list of rich output objects (plots, dataframes, etc.)

        Example:
            >>> result = await provider.run_code(
            ...     sandbox_id="sb-123",
            ...     code="import matplotlib.pyplot as plt\\nplt.plot([1,2,3])",
            ...     language="python"
            ... )
            >>> print(result['rich_outputs'])  # Contains plot data
        """
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]
            metadata["last_accessed"] = time.time()

            # Execute code with rich output capture using SDK
            result = await hopx_sandbox.run_code(
                code=code,
                language=language,
                timeout_seconds=timeout or self.timeout,
                env=env_vars,
            )

            # Convert SDK ExecutionResult to dict with rich outputs
            return {
                "success": result.success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "execution_time": result.execution_time or 0.0,
                "rich_outputs": [
                    {
                        "type": output.type,
                        "data": output.data,
                        "metadata": output.metadata,
                    }
                    for output in (result.rich_outputs or [])
                ],
            }

        except Exception as e:
            logger.error(f"Failed to execute code in sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to execute code: {e}") from e

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream execution output in real-time using WebSocket.

        Falls back to simulated streaming if WebSocket is not available.

        Args:
            sandbox_id: Sandbox ID
            command: Command to execute
            timeout: Execution timeout in seconds
            env_vars: Optional environment variables

        Yields:
            Output chunks as they are produced
        """
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]
            metadata["last_accessed"] = time.time()

            # Try to use SDK's streaming if available
            if hasattr(hopx_sandbox, "run_code_stream"):
                # Use real WebSocket streaming from SDK
                async for chunk in hopx_sandbox.run_code_stream(
                    code=command,
                    language="bash",
                    timeout_seconds=timeout or self.timeout,
                ):
                    yield chunk
            else:
                # Fallback to simulated streaming
                result = await self.execute_command(sandbox_id, command, timeout, env_vars)

                # Yield output in chunks to simulate streaming
                chunk_size = 256
                output = result.stdout

                for i in range(0, len(output), chunk_size):
                    yield output[i : i + chunk_size]
                    await asyncio.sleep(0.01)  # Small delay to simulate streaming

                if result.stderr:
                    yield f"\n[Error]: {result.stderr}"

        except Exception as e:
            logger.error(f"Failed to stream execution in sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to stream execution: {e}") from e

    async def upload_file(self, sandbox_id: str, local_path: str, sandbox_path: str) -> bool:
        """
        Upload a file to the sandbox (matches SandboxProvider interface).

        Automatically handles both text and binary files based on file extension.

        Args:
            sandbox_id: Sandbox ID
            local_path: Path to local file
            sandbox_path: Destination path in sandbox

        Returns:
            True if successful

        Raises:
            SandboxNotFoundError: If sandbox not found
            SandboxError: If upload fails

        Example:
            >>> await provider.upload_file("sb-123", "/path/to/script.py", "/workspace/script.py")
        """
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            # Validate local path to prevent path traversal attacks
            validated_path = validate_upload_path(local_path)

            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]

            # Auto-detect binary files by extension
            binary_extensions = {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".pdf",
                ".zip",
                ".tar",
                ".gz",
                ".bz2",
                ".exe",
                ".bin",
                ".so",
                ".dll",
                ".dylib",
            }
            is_binary = validated_path.suffix.lower() in binary_extensions

            # Read local file content from validated path
            if is_binary:
                content = validated_path.read_bytes()
            else:
                try:
                    content = validated_path.read_text()
                except UnicodeDecodeError:
                    # Fallback to binary if text decoding fails
                    content = validated_path.read_bytes()

            # Write to sandbox filesystem using SDK
            await hopx_sandbox.files.write(path=sandbox_path, content=content)

            logger.info(f"Uploaded {validated_path} to {sandbox_path} in sandbox {sandbox_id}")
            metadata["last_accessed"] = time.time()
            return True

        except Exception as e:
            logger.error(f"Failed to upload file to sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to upload file: {e}") from e

    async def download_file(self, sandbox_id: str, sandbox_path: str, local_path: str) -> bool:
        """
        Download a file from the sandbox (matches SandboxProvider interface).

        Automatically handles both text and binary files based on content type.

        Args:
            sandbox_id: Sandbox ID
            sandbox_path: Path to file in sandbox
            local_path: Destination path on local filesystem

        Returns:
            True if successful

        Raises:
            SandboxNotFoundError: If sandbox not found
            SandboxError: If download fails

        Example:
            >>> await provider.download_file("sb-123", "/workspace/output.txt", "/local/output.txt")
        """
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            # Validate local path to prevent path traversal attacks
            validated_path = validate_download_path(local_path)

            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]

            # Read from sandbox filesystem using SDK
            content = await hopx_sandbox.files.read(path=sandbox_path)

            # Write to local file at validated path, handling both bytes and str
            if isinstance(content, bytes):
                validated_path.write_bytes(content)
            else:
                # Content is str, write as text
                validated_path.write_text(content)

            logger.info(f"Downloaded {sandbox_path} from sandbox {sandbox_id} to {validated_path}")
            metadata["last_accessed"] = time.time()
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
                hopx_sandbox = metadata["hopx_sandbox"]
            else:
                # Try to connect to it via API
                hopx_sandbox = await HopxSandbox.connect(
                    sandbox_id, api_key=self.api_key, base_url=self.base_url
                )

            # Kill sandbox using SDK
            await hopx_sandbox.kill()

            # Remove from tracking if present
            if sandbox_id in self._sandboxes:
                async with self._lock:
                    del self._sandboxes[sandbox_id]

            logger.info(f"Destroyed Hopx sandbox {sandbox_id}")
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
        """Check if Hopx service is accessible."""
        try:
            # Try to list sandboxes as a simple health check
            sandboxes = await HopxSandbox.list(api_key=self.api_key, base_url=self.base_url)
            # Handle case where API might return None instead of empty list
            return sandboxes is not None
        except Exception as e:
            logger.error(f"Hopx health check failed: {e}")
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

    async def get_desktop_vnc_url(self, sandbox_id: str) -> str | None:
        """
        Get VNC URL for desktop automation (if available).

        Desktop automation requires sandboxes created with desktop-enabled templates.
        This feature allows GUI application testing, browser automation, and visual interactions.

        Args:
            sandbox_id: Sandbox ID

        Returns:
            VNC URL string if desktop is available, None otherwise

        Example:
            >>> # Create sandbox with desktop support
            >>> config = SandboxConfig(provider_config={"template": "desktop"})
            >>> sandbox = await provider.create_sandbox(config)
            >>>
            >>> # Get VNC URL
            >>> vnc_url = await provider.get_desktop_vnc_url(sandbox.id)
            >>> if vnc_url:
            ...     print(f"Connect to desktop at: {vnc_url}")

        Note:
            Desktop automation is an advanced feature requiring specific templates.
            Not all templates support desktop/VNC functionality.
        """
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]

            # Call SDK desktop method - it will raise DesktopNotAvailableError if not supported
            vnc_info = await hopx_sandbox.desktop.start_vnc()
            return vnc_info.url if hasattr(vnc_info, "url") else None

        except Exception as e:
            logger.error(f"Failed to get VNC URL for sandbox {sandbox_id}: {e}")
            # Don't raise, just return None - desktop might not be available
            return None

    async def screenshot(self, sandbox_id: str, output_path: str | None = None) -> bytes | None:
        """
        Capture screenshot from sandbox desktop (if available).

        Requires sandbox with desktop support.

        Args:
            sandbox_id: Sandbox ID
            output_path: Optional local path to save screenshot PNG

        Returns:
            PNG image bytes if successful, None if desktop not available

        Example:
            >>> # Capture and save screenshot
            >>> img_bytes = await provider.screenshot("sb-123", "/local/screenshot.png")
            >>> if img_bytes:
            ...     print(f"Screenshot saved: {len(img_bytes)} bytes")
        """
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]

            # Capture screenshot - SDK will raise DesktopNotAvailableError if not supported
            img_bytes = await hopx_sandbox.desktop.screenshot()

            # Optionally save to file
            if output_path and img_bytes:
                validated_path = validate_download_path(output_path)
                validated_path.write_bytes(img_bytes)
                logger.info(f"Screenshot saved to {validated_path}")

            return img_bytes

        except Exception as e:
            logger.error(f"Failed to capture screenshot for sandbox {sandbox_id}: {e}")
            return None

    async def get_preview_url(self, sandbox_id: str, port: int = 7777) -> str:
        """
        Get preview URL for accessing services running in the sandbox.

        Hopx exposes all sandbox ports via public URLs. This returns the URL
        for accessing a service on the specified port.

        Args:
            sandbox_id: Sandbox ID
            port: Port number (default: 7777 for sandbox agent)

        Returns:
            Public URL string for the service

        Raises:
            SandboxNotFoundError: If sandbox doesn't exist
            SandboxError: If URL cannot be generated

        Example:
            >>> url = await provider.get_preview_url("sb-123", 8080)
            >>> print(url)  # https://8080-sandbox123.eu-1001.vms.hopx.dev/
        """
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            hopx_sandbox = metadata["hopx_sandbox"]

            # Use SDK's get_preview_url method (requires SDK >= 0.3.0)
            url = await hopx_sandbox.get_preview_url(port)

            logger.info(f"Preview URL for sandbox {sandbox_id} port {port}: {url}")
            metadata["last_accessed"] = time.time()
            return url

        except SandboxNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to get preview URL for sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to get preview URL: {e}") from e

    async def get_agent_url(self, sandbox_id: str) -> str:
        """
        Get agent URL for the sandbox.

        Returns the public URL for the sandbox agent (port 7777).

        Args:
            sandbox_id: Sandbox ID

        Returns:
            Agent URL string

        Raises:
            SandboxNotFoundError: If sandbox doesn't exist
            SandboxError: If URL cannot be generated

        Example:
            >>> url = await provider.get_agent_url("sb-123")
        """
        return await self.get_preview_url(sandbox_id, port=7777)

    def __del__(self):
        """Cleanup on deletion."""
        # Any cleanup needed when provider is destroyed
        pass
