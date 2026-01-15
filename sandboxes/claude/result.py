"""Result dataclasses for Claude Code execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClaudeCodeResult:
    """Result from Claude Code execution.

    Attributes:
        output: Final response from Claude Code
        exit_code: Process exit code (0 = success)
        stdout: Raw stdout from the CLI
        stderr: Raw stderr from the CLI
        duration_ms: Execution duration in milliseconds
        model: Model used for execution
        sandbox_id: ID of the sandbox used
        artifacts: Files created/modified during execution
    """

    # Output
    output: str
    exit_code: int

    # Raw output
    stdout: str = ""
    stderr: str = ""

    # Metadata
    duration_ms: int = 0
    model: str | None = None
    sandbox_id: str | None = None

    # Artifacts (files created/modified)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Whether execution succeeded (exit code 0)."""
        return self.exit_code == 0

    def __str__(self) -> str:
        """Return the output as string representation."""
        return self.output


@dataclass
class ConversationTurn:
    """A single turn in an agent conversation.

    Attributes:
        prompt: The user prompt
        result: The Claude Code result
        timestamp: Unix timestamp of execution
        turn_number: Sequential turn number
    """

    prompt: str
    result: ClaudeCodeResult
    timestamp: float
    turn_number: int


@dataclass
class AgentResult(ClaudeCodeResult):
    """Result from SDK agent execution.

    Extends ClaudeCodeResult with tool usage information.

    Attributes:
        tool_calls: List of tool calls made during execution
        iterations: Number of tool-use iterations
        files_created: List of files created
        files_modified: List of files modified
    """

    # Tool usage
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0

    # File changes
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
