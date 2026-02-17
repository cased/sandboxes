"""Daytona sandbox provider implementation."""

import logging
import math
import os
from typing import Any

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError
from ..security import validate_download_path, validate_upload_path

logger = logging.getLogger(__name__)

try:
    from daytona import (
        CreateSandboxBaseParams,
        CreateSandboxFromImageParams,
        CreateSandboxFromSnapshotParams,
        Daytona,
        Resources,
    )

    DAYTONA_AVAILABLE = True
except ImportError:
    DAYTONA_AVAILABLE = False
    Daytona = None  # Define as None when not available
    CreateSandboxBaseParams = None
    CreateSandboxFromImageParams = None
    CreateSandboxFromSnapshotParams = None
    Resources = None
    logger.warning("Daytona SDK not available - install with: pip install daytona")


class DaytonaProvider(SandboxProvider):
    """Daytona sandbox provider implementation."""

    def __init__(self, api_key: str | None = None, **config):
        """Initialize Daytona provider."""
        super().__init__(**config)

        if not DAYTONA_AVAILABLE:
            raise ProviderError("Daytona SDK not installed")

        self.api_key = api_key or os.getenv("DAYTONA_API_KEY")
        if not self.api_key:
            raise ProviderError("Daytona API key not provided")

        self.client = Daytona()
        # Default to Daytona's AI-optimized image with pre-installed packages
        # Includes Python 3.13, numpy, requests, and many AI/ML packages
        # Users can override via config.image or provider_config
        self.default_image = config.get("default_image", "daytonaio/ai-test:0.2.3")
        # Fallback language for CreateSandboxBaseParams
        self.default_language = config.get("default_language", "python")
        # Keep snapshot support for backwards compatibility
        self.default_snapshot = config.get("default_snapshot")
        # Track sandbox metadata including env_vars
        self._sandbox_metadata: dict[str, dict] = {}

    @property
    def name(self) -> str:
        """Provider name."""
        return "daytona"

    def _convert_state(self, daytona_state: str) -> SandboxState:
        """Convert Daytona state to standard state."""
        state_map = {
            "started": SandboxState.RUNNING,
            "running": SandboxState.RUNNING,
            "starting": SandboxState.STARTING,
            "stopped": SandboxState.STOPPED,
            "stopping": SandboxState.STOPPING,
            "terminated": SandboxState.TERMINATED,
            "error": SandboxState.ERROR,
        }
        return state_map.get(daytona_state.lower(), SandboxState.ERROR)

    def _to_sandbox(self, daytona_sandbox: Any) -> Sandbox:
        """Convert Daytona sandbox to standard Sandbox."""
        return Sandbox(
            id=daytona_sandbox.id,
            provider=self.name,
            state=self._convert_state(daytona_sandbox.state),
            labels=getattr(daytona_sandbox, "labels", {}),
            created_at=getattr(daytona_sandbox, "created_at", None),
            metadata={
                "state_raw": daytona_sandbox.state,
                "snapshot": getattr(daytona_sandbox, "snapshot", None),
            },
        )

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new sandbox."""
        try:
            # Priority order:
            # 1. Snapshot (if explicitly provided) - backwards compatibility
            # 2. Docker image (most portable) - RECOMMENDED
            # 3. Language (fallback)

            snapshot = (
                config.provider_config.get("snapshot") if config.provider_config else None
            ) or self.default_snapshot

            if snapshot:
                # Use snapshot-based creation (legacy/backwards compatibility)
                logger.info(f"Creating Daytona sandbox with snapshot: {snapshot}")
                params = CreateSandboxFromSnapshotParams(
                    snapshot=snapshot, labels=config.labels or {}
                )
            elif config.image or (config.provider_config and "image" in config.provider_config):
                # Use Docker image (RECOMMENDED - most portable)
                image = config.image or config.provider_config.get("image") or self.default_image
                logger.info(f"Creating Daytona sandbox with Docker image: {image}")

                # Configure resources if specified
                resources = None
                if config.memory_mb or config.cpu_cores:
                    memory_gib = None
                    if config.memory_mb:
                        # Daytona resources.memory is GiB; round up to avoid 0 GiB allocations.
                        memory_gib = max(1, math.ceil(config.memory_mb / 1024))
                    resources = Resources(
                        cpu=int(config.cpu_cores) if config.cpu_cores else None,
                        memory=memory_gib,
                    )
                    logger.info(
                        f"Configuring resources: CPU={config.cpu_cores}, Memory={config.memory_mb}MB"
                    )

                params = CreateSandboxFromImageParams(
                    image=image,
                    resources=resources,
                    labels=config.labels or {},
                    env_vars=config.env_vars or {},
                )
            else:
                # Use language-based creation (fallback)
                language = (
                    config.provider_config.get("language") if config.provider_config else None
                ) or self.default_language
                logger.info(f"Creating Daytona sandbox with language: {language}")
                params = CreateSandboxBaseParams(language=language, labels=config.labels or {})

            # Create sandbox with timeout
            # Use config timeout or default to 120 seconds (Daytona default is 60)
            timeout = config.timeout_seconds or 120
            daytona_sandbox = self.client.create(params, timeout=timeout)
            logger.info(f"Created Daytona sandbox {daytona_sandbox.id}")

            sandbox = self._to_sandbox(daytona_sandbox)

            # Store env_vars for use in each command execution
            self._sandbox_metadata[sandbox.id] = {
                "env_vars": config.env_vars or {},
            }

            # Run setup commands if provided
            if config.setup_commands:
                for cmd in config.setup_commands:
                    await self.execute_command(sandbox.id, cmd)

            return sandbox

        except Exception as e:
            logger.error(f"Failed to create Daytona sandbox: {e}")
            raise SandboxError(f"Failed to create sandbox: {e}") from e

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID."""
        try:
            daytona_sandbox = self.client.get(sandbox_id)
            return self._to_sandbox(daytona_sandbox)
        except Exception as e:
            if "not found" in str(e).lower():
                return None
            logger.error(f"Failed to get sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to get sandbox: {e}") from e

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List sandboxes, optionally filtered by labels."""
        try:
            # Daytona's list() returns a PaginatedSandboxes object with items attribute
            daytona_response = self.client.list(labels=labels) if labels else self.client.list()

            # Extract the actual list of sandboxes from the paginated response
            daytona_sandboxes = (
                daytona_response.items
                if hasattr(daytona_response, "items")
                else list(daytona_response)
            )

            return [self._to_sandbox(s) for s in daytona_sandboxes]
        except Exception as e:
            logger.error(f"Failed to list sandboxes: {e}")
            raise SandboxError(f"Failed to list sandboxes: {e}") from e

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a command in a sandbox."""
        try:
            sandbox = self.client.get(sandbox_id)

            # Combine stored env_vars with any passed env_vars
            all_env_vars = dict(self._sandbox_metadata.get(sandbox_id, {}).get("env_vars", {}))
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

                exports = " && ".join(
                    [
                        f"export {validate_env_key(k)}='{escape_shell_value(str(v))}'"
                        for k, v in all_env_vars.items()
                    ]
                )
                command = f"{exports} && {command}"

            # Execute command using process.exec
            result = sandbox.process.exec(command)

            return ExecutionResult(
                exit_code=result.exit_code,
                stdout=result.result or "",  # Daytona uses 'result' for output
                stderr="" if result.exit_code == 0 else (result.result or ""),
                truncated=False,
                timed_out=False,
            )

        except Exception as e:
            if "not found" in str(e).lower():
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found") from e
            logger.error(f"Failed to execute command in sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to execute command: {e}") from e

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox."""
        try:
            sandbox = self.client.get(sandbox_id)
            sandbox.delete()  # Daytona uses delete() not destroy()
            logger.info(f"Destroyed Daytona sandbox {sandbox_id}")
            return True
        except Exception as e:
            if "not found" in str(e).lower():
                return False
            logger.error(f"Failed to destroy sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to destroy sandbox: {e}") from e

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find a running sandbox with matching labels (smart reuse from comet)."""
        try:
            sandboxes = await self.list_sandboxes(labels=labels)
            # Only return running/started sandboxes
            running = [
                s for s in sandboxes if s.state in [SandboxState.RUNNING, SandboxState.STARTING]
            ]
            if running:
                logger.info(f"Found existing running sandbox {running[0].id} with labels {labels}")
                return running[0]

            # Log info about non-running sandboxes
            stopped = [
                s for s in sandboxes if s.state not in [SandboxState.RUNNING, SandboxState.STARTING]
            ]
            if stopped:
                logger.info(f"Found {len(stopped)} non-running sandboxes with labels {labels}")

            return None
        except Exception as e:
            logger.error(f"Failed to find sandbox with labels {labels}: {e}")
            return None

    async def upload_file(self, sandbox_id: str, local_path: str, sandbox_path: str) -> bool:
        """Upload a file to the sandbox."""
        try:
            # Validate local path to prevent path traversal attacks
            validated_path = validate_upload_path(local_path)

            # Get the sandbox
            sandbox = self.client.get(sandbox_id)

            # Read local file content from validated path
            with open(validated_path, "rb") as f:
                content = f.read()

            # Upload to sandbox using fs.upload_file
            # Daytona's upload_file accepts src as string path or bytes
            sandbox.fs.upload_file(src=content, dst=sandbox_path)

            logger.info(f"Uploaded {validated_path} to {sandbox_path} in sandbox {sandbox_id}")
            return True

        except Exception as e:
            if "not found" in str(e).lower():
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found") from e
            logger.error(f"Failed to upload file to sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to upload file: {e}") from e

    async def download_file(self, sandbox_id: str, sandbox_path: str, local_path: str) -> bool:
        """Download a file from the sandbox."""
        try:
            # Validate local path to prevent path traversal attacks
            validated_path = validate_download_path(local_path)

            # Get the sandbox
            sandbox = self.client.get(sandbox_id)

            # Download from sandbox using fs.download_file
            content = sandbox.fs.download_file(sandbox_path)

            if content is None:
                raise SandboxError(f"File {sandbox_path} not found in sandbox")

            # Write to local file at validated path
            with open(validated_path, "wb") as f:
                f.write(content)

            logger.info(f"Downloaded {sandbox_path} from sandbox {sandbox_id} to {validated_path}")
            return True

        except Exception as e:
            if "not found" in str(e).lower():
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found") from e
            logger.error(f"Failed to download file from sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to download file: {e}") from e
