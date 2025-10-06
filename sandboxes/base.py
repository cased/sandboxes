"""Base abstractions for sandbox providers."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional


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
    image: Optional[str] = None  # Docker image or template name
    language: Optional[str] = None  # For providers that work with language runtimes

    # Resource limits
    memory_mb: Optional[int] = None
    cpu_cores: Optional[float] = None
    timeout_seconds: Optional[int] = 120

    # Environment
    env_vars: Dict[str, str] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)  # For finding/reusing sandboxes

    # Provider-specific configuration
    provider_config: Dict[str, Any] = field(default_factory=dict)

    # Setup
    setup_commands: List[str] = field(default_factory=list)  # Commands to run on creation
    working_dir: Optional[str] = None

    def model_copy(self, update: Optional[Dict[str, Any]] = None) -> "SandboxConfig":
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
    duration_ms: Optional[int] = None

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
    labels: Dict[str, str] = field(default_factory=dict)
    created_at: Optional[datetime] = None

    # Connection info (provider-specific)
    connection_info: Dict[str, Any] = field(default_factory=dict)

    # Provider-specific metadata
    metadata: Dict[str, Any] = field(default_factory=dict)


class SandboxProvider(ABC):
    """Abstract base class for sandbox providers."""

    def __init__(self, **config):
        """Initialize provider with configuration."""
        self.config = config

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
    async def get_sandbox(self, sandbox_id: str) -> Optional[Sandbox]:
        """Get sandbox by ID."""
        pass

    @abstractmethod
    async def list_sandboxes(self, labels: Optional[Dict[str, str]] = None) -> List[Sandbox]:
        """List sandboxes, optionally filtered by labels."""
        pass

    @abstractmethod
    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: Optional[int] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """Execute a command in a sandbox."""
        pass

    @abstractmethod
    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox."""
        pass

    # Optional methods with default implementations

    async def find_sandbox(self, labels: Dict[str, str]) -> Optional[Sandbox]:
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
        commands: List[str],
        stop_on_error: bool = True,
        timeout: Optional[int] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> List[ExecutionResult]:
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
        timeout: Optional[int] = None,
        env_vars: Optional[Dict[str, str]] = None,
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
