"""
Claude Code integration for sandboxes.

Provides a layered API for running Claude Code in isolated sandbox environments:

Layer 1 - Simple helper:
    >>> result = await run_claude_code("Create a hello.py file")

Layer 2 - Agent with lifecycle:
    >>> async with ClaudeCodeAgent() as agent:
    ...     await agent.run("Create a Flask app")
    ...     await agent.run("Add authentication")

Layer 3 - SDK integration:
    >>> async with ClaudeCodeSDK() as sdk:
    ...     result = await sdk.run_agent("Build a scraper", tools=["Bash"])
"""

# Layer 2: Agent class
from .agent import ClaudeCodeAgent
from .config import AgentConfig, ClaudeCodeConfig
from .exceptions import (
    ClaudeCodeError,
    ClaudeCodeNotInstalledError,
    ClaudeCodeSetupError,
    ClaudeCodeTimeoutError,
)
from .result import AgentResult, ClaudeCodeResult

# Layer 1: Simple helper
from .runner import run_claude_code, stream_claude_code

# Layer 3: SDK integration
from .sdk import ClaudeCodeSDK

# Setup helpers
from .setup import get_modal_claude_image

__all__ = [
    # Config
    "ClaudeCodeConfig",
    "AgentConfig",
    # Results
    "ClaudeCodeResult",
    "AgentResult",
    # Exceptions
    "ClaudeCodeError",
    "ClaudeCodeSetupError",
    "ClaudeCodeTimeoutError",
    "ClaudeCodeNotInstalledError",
    # Layer 1
    "run_claude_code",
    "stream_claude_code",
    # Layer 2
    "ClaudeCodeAgent",
    # Layer 3
    "ClaudeCodeSDK",
    # Setup helpers
    "get_modal_claude_image",
]
