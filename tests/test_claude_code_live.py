"""Live tests for Claude Code integration (requires real API keys)."""

import os

import pytest

from sandboxes.claude import ClaudeCodeAgent, run_claude_code

# Check which providers are available
HAS_E2B = bool(os.getenv("E2B_API_KEY"))
HAS_ANTHROPIC = bool(os.getenv("ANTHROPIC_API_KEY"))
HAS_DAYTONA = bool(os.getenv("DAYTONA_API_KEY"))

# Skip all tests if no Anthropic key
pytestmark = pytest.mark.skipif(
    not HAS_ANTHROPIC,
    reason="ANTHROPIC_API_KEY required for live tests",
)


class TestClaudeCodeE2B:
    """Live tests with E2B provider."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_E2B, reason="E2B_API_KEY not set")
    async def test_simple_prompt(self):
        """Test a simple prompt execution with E2B."""
        result = await run_claude_code(
            "What is 2 + 2? Reply with just the number.",
            provider="e2b",
            timeout=600,
        )

        assert result.success, f"Failed: {result.stderr}"
        assert result.exit_code == 0
        assert "4" in result.output
        print(f"\nOutput: {result.output}")
        print(f"Duration: {result.duration_ms}ms")

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_E2B, reason="E2B_API_KEY not set")
    async def test_file_creation(self):
        """Test Claude Code creating a file."""
        result = await run_claude_code(
            "Create a file called hello.py that prints 'Hello from Claude'. "
            "Then run it and show the output.",
            provider="e2b",
            timeout=600,
        )

        assert result.success, f"Failed: {result.stderr}"
        print(f"\nOutput: {result.output}")

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_E2B, reason="E2B_API_KEY not set")
    async def test_agent_multi_turn(self):
        """Test multi-turn conversation with E2B."""
        async with ClaudeCodeAgent(provider="e2b", timeout=600) as agent:
            # First turn
            r1 = await agent.run("Create a file called count.txt with the number 1")
            assert r1.success, f"Turn 1 failed: {r1.stderr}"
            print(f"\nTurn 1: {r1.output[:200]}")

            # Second turn - verify persistence
            r2 = await agent.run("Read count.txt and tell me what number is in it")
            assert r2.success, f"Turn 2 failed: {r2.stderr}"
            assert "1" in r2.output
            print(f"Turn 2: {r2.output[:200]}")

            assert len(agent.conversation_history) == 2


class TestClaudeCodeDaytona:
    """Live tests with Daytona provider."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_DAYTONA, reason="DAYTONA_API_KEY not set")
    async def test_simple_prompt(self):
        """Test a simple prompt execution with Daytona."""
        result = await run_claude_code(
            "What is 2 + 2? Reply with just the number.",
            provider="daytona",
            timeout=600,
        )

        assert result.success, f"Failed: {result.stderr}"
        assert "4" in result.output
        print(f"\nOutput: {result.output}")
        print(f"Duration: {result.duration_ms}ms")

    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_DAYTONA, reason="DAYTONA_API_KEY not set")
    async def test_agent_lifecycle(self):
        """Test agent lifecycle with Daytona."""
        async with ClaudeCodeAgent(provider="daytona", timeout=600) as agent:
            assert agent.is_running
            assert agent.sandbox is not None

            result = await agent.run("Echo 'hello from daytona'")
            assert result.success
            print(f"\nOutput: {result.output}")
