"""Base abstractions for sandbox providers."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SandboxState(Enum):
    """Standard states for sandboxes across providers."""

    CREATING = "creating"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    TERMINATED = "terminated"
    ERROR = "error"


@dataclass
class SandboxConfig:
    """Configuration for creating a sandbox."""

    # Core configuration
    image: str | None = None  # Docker image or template name
    language: str | None = None  # For providers that work with language runtimes

    # Resource limits
    memory_mb: int | None = None
    cpu_cores: float | None = None
    timeout_seconds: int | None = 120

    # Environment
    env_vars: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)  # For finding/reusing sandboxes

    # Provider-specific configuration
    provider_config: dict[str, Any] = field(default_factory=dict)

    # Setup
    setup_commands: list[str] = field(default_factory=list)  # Commands to run on creation
    working_dir: str | None = None

    def model_copy(self, update: dict[str, Any] | None = None) -> "SandboxConfig":
        """Return a new config with optional updated fields (pydantic-like)."""
        data = asdict(self)
        if update:
            data.update(update)
        return SandboxConfig(**data)


@dataclass
class ExecutionResult:
    """Result of executing a command in a sandbox."""

    exit_code: int
    stdout: str
    stderr: str

    # Timing information
    duration_ms: int | None = None

    # Metadata
    truncated: bool = False
    timed_out: bool = False

    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.exit_code == 0 and not self.timed_out


@dataclass
class Sandbox:
    """Representation of a sandbox instance."""

    id: str
    provider: str
    state: SandboxState

    # Metadata
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime | None = None

    # Connection info (provider-specific)
    connection_info: dict[str, Any] = field(default_factory=dict)

    # Provider-specific metadata
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Feature flags exposed by a provider."""

    persistent: bool = False
    snapshot: bool = False
    streaming: bool = False
    file_upload: bool = False
    interactive_shell: bool = False
    gpu: bool = False

    def as_dict(self) -> dict[str, bool]:
        """Return capabilities as a plain dictionary."""
        return asdict(self)


class SandboxProvider(ABC):
    """Abstract base class for sandbox providers."""

    CAPABILITIES = ProviderCapabilities()

    def __init__(self, **config):
        """Initialize provider with configuration."""
        self.config = config

    @classmethod
    def get_capabilities(cls) -> ProviderCapabilities:
        """Return static capabilities for this provider class."""
        return cls.CAPABILITIES

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return capabilities for this provider instance."""
        return self.get_capabilities()

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'daytona', 'e2b', 'modal')."""
        pass

    @abstractmethod
    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new sandbox."""
        pass

    @abstractmethod
    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID."""
        pass

    @abstractmethod
    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List sandboxes, optionally filtered by labels."""
        pass

    @abstractmethod
    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a command in a sandbox."""
        pass

    @abstractmethod
    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox."""
        pass

    # Optional methods with default implementations

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find a running sandbox with matching labels."""
        sandboxes = await self.list_sandboxes(labels=labels)
        running = [s for s in sandboxes if s.state == SandboxState.RUNNING]
        return running[0] if running else None

    async def get_or_create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Get existing sandbox with matching labels or create new one."""
        if config.labels:
            existing = await self.find_sandbox(config.labels)
            if existing:
                return existing
        return await self.create_sandbox(config)

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
                break
        return results

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream command output (if supported by provider)."""
        # Default implementation just returns the full output
        result = await self.execute_command(sandbox_id, command, timeout, env_vars)
        if result.stdout:
            yield result.stdout
        if result.stderr:
            yield result.stderr

    async def upload_file(
        self,
        sandbox_id: str,
        local_path: str,
        sandbox_path: str,
    ) -> bool:
        """Upload a file to the sandbox (if supported)."""
        raise NotImplementedError(f"{self.name} does not support file uploads")

    async def download_file(
        self,
        sandbox_id: str,
        sandbox_path: str,
        local_path: str,
    ) -> bool:
        """Download a file from the sandbox (if supported)."""
        raise NotImplementedError(f"{self.name} does not support file downloads")

    async def health_check(self) -> bool:
        """Check if provider is healthy and accessible."""
        try:
            await self.list_sandboxes()
            return True
        except Exception:
            return False
