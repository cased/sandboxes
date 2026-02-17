"""
Sandboxes - Universal AI Code Execution

A unified interface for AI code execution sandboxes across multiple providers.
"""

__version__ = "0.4.2"

from .base import (
    ExecutionResult,
    ProviderCapabilities,
    SandboxConfig,
    SandboxProvider,
    SandboxState,
)
from .base import Sandbox as BaseSandbox
from .exceptions import (
    ProviderError,
    SandboxAuthenticationError,
    SandboxError,
    SandboxNotFoundError,
    SandboxQuotaError,
    SandboxTimeoutError,
)
from .manager import SandboxManager
from .pool import PoolConfig, PoolStrategy, SandboxPool
from .retry import CircuitBreaker, RetryConfig, RetryHandler, with_retry
from .sandbox import Sandbox, run, run_many

# Alias for convenience
Manager = SandboxManager

__all__ = [
    # High-level interface
    "Sandbox",
    "run",
    "run_many",
    # Core types
    "SandboxProvider",
    "BaseSandbox",
    "SandboxConfig",
    "ExecutionResult",
    "SandboxState",
    "ProviderCapabilities",
    # Manager
    "SandboxManager",
    "Manager",  # Alias
    # Pooling
    "SandboxPool",
    "PoolConfig",
    "PoolStrategy",
    # Retry and resilience
    "RetryHandler",
    "RetryConfig",
    "with_retry",
    "CircuitBreaker",
    # Exceptions
    "SandboxError",
    "SandboxNotFoundError",
    "SandboxTimeoutError",
    "ProviderError",
    "SandboxQuotaError",
    "SandboxAuthenticationError",
]
