"""Claude Code specific exceptions."""

from __future__ import annotations

from ..exceptions import SandboxError


class ClaudeCodeError(SandboxError):
    """Base exception for Claude Code errors."""

    pass


class ClaudeCodeSetupError(ClaudeCodeError):
    """Error during Claude Code setup in sandbox."""

    pass


class ClaudeCodeNotInstalledError(ClaudeCodeSetupError):
    """Claude Code CLI is not installed in the sandbox."""

    pass


class ClaudeCodeTimeoutError(ClaudeCodeError):
    """Claude Code execution timed out."""

    pass


class ClaudeCodeAPIKeyError(ClaudeCodeError):
    """Anthropic API key is missing or invalid."""

    pass
