"""Exception classes for sandbox operations."""


class SandboxError(Exception):
    """Base exception for sandbox-related errors."""

    pass


class ProviderError(SandboxError):
    """Error related to the sandbox provider."""

    pass


class SandboxNotFoundError(SandboxError):
    """Sandbox with given ID not found."""

    pass


class SandboxTimeoutError(SandboxError):
    """Operation timed out."""

    pass


class SandboxStateError(SandboxError):
    """Sandbox is in incorrect state for operation."""

    pass


class SandboxQuotaError(SandboxError):
    """Provider quota exceeded."""

    pass


class SandboxAuthenticationError(ProviderError):
    """Authentication failed with provider."""

    pass
