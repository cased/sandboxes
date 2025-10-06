"""Daytona sandbox provider implementation."""

import logging
import os
from typing import Any

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError

logger = logging.getLogger(__name__)

try:
    from daytona import (
        CreateSandboxBaseParams,
        CreateSandboxFromImageParams,
        CreateSandboxFromSnapshotParams,
        Daytona,
    )

    DAYTONA_AVAILABLE = True
except ImportError:
    DAYTONA_AVAILABLE = False
    Daytona = None  # Define as None when not available
    CreateSandboxBaseParams = None
    CreateSandboxFromImageParams = None
    CreateSandboxFromSnapshotParams = None
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
                params = CreateSandboxFromImageParams(image=image)
            else:
                # Use language-based creation (fallback)
                language = (
                    config.provider_config.get("language") if config.provider_config else None
                ) or self.default_language
                logger.info(f"Creating Daytona sandbox with language: {language}")
                params = CreateSandboxBaseParams(language=language, labels=config.labels or {})

            # Create sandbox
            daytona_sandbox = self.client.create(params)
            logger.info(f"Created Daytona sandbox {daytona_sandbox.id}")

            sandbox = self._to_sandbox(daytona_sandbox)

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

            # Prepare command with environment variables
            if env_vars:
                exports = " && ".join([f"export {k}='{v}'" for k, v in env_vars.items()])
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
