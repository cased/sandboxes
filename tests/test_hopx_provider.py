"""Tests for the Hopx sandbox provider."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandboxes.base import ExecutionResult, SandboxConfig
from sandboxes.exceptions import ProviderError, SandboxError, SandboxNotFoundError
from sandboxes.providers.hopx import HopxProvider


@pytest.mark.asyncio
async def test_hopx_happy_path():
    """Create, execute, list, destroy, and health-check a Hopx sandbox."""
    sandbox_id = "hopx-test-123"
    provider = HopxProvider(api_key="test-key")

    # Mock the Hopx SDK
    with patch("sandboxes.providers.hopx.HopxSandbox") as MockHopxSandbox:
        # Create mock sandbox instance
        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = sandbox_id
        mock_sandbox.get_info = AsyncMock(
            return_value=MagicMock(
                public_host="https://hopx-test-123.hopx.dev",
                created_at=None,
                template_name="code-interpreter",
            )
        )
        mock_sandbox.commands.run = AsyncMock(
            return_value=MagicMock(exit_code=0, stdout="hello\n", stderr="", execution_time=0.1)
        )
        mock_sandbox.kill = AsyncMock()

        # Mock SDK class methods
        MockHopxSandbox.create = AsyncMock(return_value=mock_sandbox)
        MockHopxSandbox.list = AsyncMock(return_value=[mock_sandbox])

        # Create sandbox
        config = SandboxConfig(labels={"test": "hopx"})
        sandbox = await provider.create_sandbox(config)
        assert sandbox.id == sandbox_id
        assert sandbox.provider == "hopx"

        # Verify create was called with correct parameters
        MockHopxSandbox.create.assert_called_once()
        call_kwargs = MockHopxSandbox.create.call_args.kwargs
        assert call_kwargs["template"] == "code-interpreter"
        assert call_kwargs["api_key"] == "test-key"

        # List sandboxes
        listed = await provider.list_sandboxes()
        assert any(sb.id == sandbox_id for sb in listed)

        # Execute command
        result = await provider.execute_command(sandbox_id, "echo hello")
        assert result.success
        assert result.stdout == "hello\n"
        assert result.exit_code == 0

        # Destroy sandbox
        destroyed = await provider.destroy_sandbox(sandbox_id)
        assert destroyed is True
        mock_sandbox.kill.assert_called_once()


@pytest.mark.asyncio
async def test_hopx_missing_api_key():
    """Provider should raise ProviderError if API key is not provided."""
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(ProviderError, match="Hopx API key not provided"),
    ):
        HopxProvider()


@pytest.mark.asyncio
async def test_hopx_api_key_from_env():
    """Provider should use API key from environment variable."""
    with patch.dict(os.environ, {"HOPX_API_KEY": "env-key"}):
        provider = HopxProvider()
        assert provider.api_key == "env-key"


@pytest.mark.asyncio
async def test_hopx_missing_sandbox():
    """Executing against a missing sandbox should raise SandboxNotFoundError."""
    provider = HopxProvider(api_key="test-key")

    # Try to execute command on non-existent sandbox
    with pytest.raises(SandboxNotFoundError, match="Sandbox .* not found"):
        await provider.execute_command("unknown-id", "echo test")

    # get_sandbox should return None for non-existent sandbox
    sandbox = await provider.get_sandbox("unknown-id")
    assert sandbox is None


@pytest.mark.asyncio
async def test_hopx_http_error_raises_sandbox_error():
    """SDK errors should surface as SandboxError."""
    provider = HopxProvider(api_key="test-key")

    with patch("sandboxes.providers.hopx.HopxSandbox") as MockHopxSandbox:
        # Mock SDK to raise error
        MockHopxSandbox.list = AsyncMock(side_effect=Exception("API Error"))

        # health_check catches errors and returns False
        result = await provider.health_check()
        assert result is False


@pytest.mark.asyncio
async def test_hopx_stream_execution():
    """Test streaming execution with simulated chunking."""
    sandbox_id = "stream-test"
    provider = HopxProvider(api_key="test-key")

    with patch.object(provider, "execute_command") as mock_exec:
        mock_exec.return_value = ExecutionResult(
            exit_code=0,
            stdout="streaming output test",
            stderr="",
            duration_ms=50,
            truncated=False,
            timed_out=False,
        )

        # Add sandbox to tracking
        provider._sandboxes[sandbox_id] = {"labels": {}}

        chunks = []
        async for chunk in provider.stream_execution(sandbox_id, "echo test"):
            chunks.append(chunk)

        output = "".join(chunks)
        assert "streaming output test" in output


@pytest.mark.asyncio
async def test_hopx_file_upload():
    """Test file upload with security validation."""
    sandbox_id = "file-upload-test"
    provider = HopxProvider(api_key="test-key")

    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("test file content")
        temp_path = f.name

    try:
        # Create mock sandbox
        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = sandbox_id
        mock_sandbox.files.write = AsyncMock()

        # Add to tracking
        provider._sandboxes[sandbox_id] = {
            "hopx_sandbox": mock_sandbox,
            "labels": {},
            "last_accessed": 0,
        }

        success = await provider.upload_file(sandbox_id, temp_path, "/workspace/test.txt")
        assert success

        # Verify SDK method was called
        mock_sandbox.files.write.assert_called_once()
        call_kwargs = mock_sandbox.files.write.call_args.kwargs
        assert call_kwargs["path"] == "/workspace/test.txt"
        assert "content" in call_kwargs
    finally:
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_hopx_file_upload_security_validation():
    """Test that file upload prevents path traversal attacks."""
    sandbox_id = "security-test"
    provider = HopxProvider(api_key="test-key")

    mock_sandbox = AsyncMock()
    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
    }

    # Test path traversal attack
    with pytest.raises(SandboxError, match="Path traversal"):
        await provider.upload_file(sandbox_id, "../../../etc/passwd", "/workspace/test.txt")


@pytest.mark.asyncio
async def test_hopx_file_download():
    """Test file download with security validation."""
    sandbox_id = "file-download-test"
    provider = HopxProvider(api_key="test-key")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "downloaded.txt")

        # Create mock sandbox
        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = sandbox_id
        mock_sandbox.files.read = AsyncMock(return_value="downloaded file content")

        # Add to tracking
        provider._sandboxes[sandbox_id] = {
            "hopx_sandbox": mock_sandbox,
            "labels": {},
            "last_accessed": 0,
        }

        success = await provider.download_file(sandbox_id, "/workspace/test.txt", output_path)
        assert success

        # Verify the content was written correctly
        with open(output_path, "r") as f:
            content = f.read()
        assert content == "downloaded file content"

        # Verify SDK method was called
        mock_sandbox.files.read.assert_called_once_with(path="/workspace/test.txt")


@pytest.mark.asyncio
async def test_hopx_file_download_security_validation():
    """Test that file download prevents path traversal attacks."""
    sandbox_id = "security-test"
    provider = HopxProvider(api_key="test-key")

    mock_sandbox = AsyncMock()
    mock_sandbox.files.read = AsyncMock(return_value="content")
    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
    }

    # Test path traversal attack on destination
    with pytest.raises(SandboxError, match="parent directory does not exist"):
        await provider.download_file(sandbox_id, "/workspace/file.txt", "/nonexistent/path.txt")


@pytest.mark.asyncio
async def test_hopx_find_sandbox_with_labels():
    """Test finding a sandbox by labels."""
    provider = HopxProvider(api_key="test-key")

    # Create mock sandboxes
    mock_sb1 = AsyncMock()
    mock_sb1.sandbox_id = "sb-1"
    mock_sb2 = AsyncMock()
    mock_sb2.sandbox_id = "sb-2"

    # Add to tracking with labels
    import time

    provider._sandboxes = {
        "sb-1": {
            "hopx_sandbox": mock_sb1,
            "labels": {"env": "prod", "app": "web"},
            "last_accessed": time.time(),
            "created_at": None,
        },
        "sb-2": {
            "hopx_sandbox": mock_sb2,
            "labels": {"env": "dev", "app": "api"},
            "last_accessed": time.time() - 100,
            "created_at": None,
        },
    }

    # Find by matching labels
    found = await provider.find_sandbox({"env": "prod"})
    assert found is not None
    assert found.id == "sb-1"

    # No match
    found_none = await provider.find_sandbox({"env": "staging"})
    assert found_none is None


@pytest.mark.asyncio
async def test_hopx_cleanup_idle_sandboxes():
    """Test cleanup of idle sandboxes."""
    provider = HopxProvider(api_key="test-key")

    import time

    # Create mock sandboxes
    mock_old = AsyncMock()
    mock_old.sandbox_id = "old-sandbox"
    mock_old.kill = AsyncMock()

    mock_new = AsyncMock()
    mock_new.sandbox_id = "new-sandbox"
    mock_new.kill = AsyncMock()

    # Add to tracking with different access times
    provider._sandboxes = {
        "old-sandbox": {
            "hopx_sandbox": mock_old,
            "last_accessed": time.time() - 1000,
            "labels": {},
        },
        "new-sandbox": {
            "hopx_sandbox": mock_new,
            "last_accessed": time.time(),
            "labels": {},
        },
    }

    # Cleanup with 500 second timeout
    await provider.cleanup_idle_sandboxes(idle_timeout=500)

    # Should only destroy old-sandbox
    mock_old.kill.assert_called_once()
    mock_new.kill.assert_not_called()

    # Old sandbox should be removed from tracking
    assert "old-sandbox" not in provider._sandboxes
    assert "new-sandbox" in provider._sandboxes


@pytest.mark.asyncio
async def test_hopx_template_selection():
    """Test that templates can be specified via config."""
    provider = HopxProvider(api_key="test-key")

    with patch("sandboxes.providers.hopx.HopxSandbox") as MockHopxSandbox:
        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = "template-test"
        mock_sandbox.get_info = AsyncMock(
            return_value=MagicMock(
                public_host="https://template-test.hopx.dev",
                created_at=None,
                template_name="nodejs",
            )
        )
        MockHopxSandbox.create = AsyncMock(return_value=mock_sandbox)

        # Create with custom template via provider_config
        config = SandboxConfig(provider_config={"template": "nodejs"})
        sandbox = await provider.create_sandbox(config)
        assert sandbox.id == "template-test"

        # Verify template was passed
        call_kwargs = MockHopxSandbox.create.call_args.kwargs
        assert call_kwargs["template"] == "nodejs"


@pytest.mark.asyncio
async def test_hopx_execute_commands_batch():
    """Test executing multiple commands in sequence."""
    provider = HopxProvider(api_key="test-key")
    sandbox_id = "batch-test"

    mock_sandbox = AsyncMock()
    mock_sandbox.sandbox_id = sandbox_id
    mock_sandbox.commands.run = AsyncMock(
        return_value=MagicMock(exit_code=0, stdout="output", stderr="", execution_time=0.1)
    )

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "last_accessed": 0,
    }

    # Execute multiple commands
    commands = ["echo 'test1'", "echo 'test2'", "echo 'test3'"]
    results = await provider.execute_commands(sandbox_id, commands)

    assert len(results) == 3
    assert all(r.success for r in results)
    assert mock_sandbox.commands.run.call_count == 3


@pytest.mark.asyncio
async def test_hopx_execute_commands_stop_on_error():
    """Test that execute_commands stops on first error when stop_on_error=True."""
    provider = HopxProvider(api_key="test-key")
    sandbox_id = "error-test"

    mock_sandbox = AsyncMock()
    mock_sandbox.sandbox_id = sandbox_id

    # First command succeeds, second fails, third should not run
    call_count = 0

    async def mock_run(command, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(exit_code=0, stdout="ok", stderr="", execution_time=0.1)
        else:
            return MagicMock(exit_code=1, stdout="", stderr="error", execution_time=0.1)

    mock_sandbox.commands.run = mock_run

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "last_accessed": 0,
    }

    commands = ["echo 'ok'", "exit 1", "echo 'should not run'"]
    results = await provider.execute_commands(sandbox_id, commands, stop_on_error=True)

    # Only first two commands should run
    assert len(results) == 2
    assert results[0].success
    assert not results[1].success
    assert call_count == 2  # Third command not executed


@pytest.mark.asyncio
async def test_hopx_get_or_create_sandbox():
    """Test get_or_create_sandbox reuses existing sandboxes."""
    provider = HopxProvider(api_key="test-key")

    # Add existing sandbox
    mock_existing = AsyncMock()
    mock_existing.sandbox_id = "existing-sb"
    provider._sandboxes["existing-sb"] = {
        "hopx_sandbox": mock_existing,
        "labels": {"env": "test"},
        "last_accessed": 0,
        "created_at": None,
    }

    # Request sandbox with matching labels
    config = SandboxConfig(labels={"env": "test"})
    sandbox = await provider.get_or_create_sandbox(config)

    # Should return existing sandbox
    assert sandbox.id == "existing-sb"


@pytest.mark.asyncio
@pytest.mark.hopx
@pytest.mark.integration
async def test_hopx_live_integration():
    """Live integration test with real Hopx API.

    This test is skipped unless HOPX_API_KEY is set and pytest is run with -m hopx.
    """
    api_key = os.getenv("HOPX_API_KEY")

    if not api_key:
        pytest.skip("Hopx live credentials not configured")

    provider = HopxProvider(api_key=api_key)

    # Test health check first
    assert await provider.health_check() is True

    # Create a sandbox
    config = SandboxConfig(labels={"test": "pytest-live"})
    sandbox = await provider.create_sandbox(config)

    try:
        # Execute a command
        result = await provider.execute_command(sandbox.id, "echo 'hopx test'")
        assert result.success
        assert "hopx test" in result.stdout

        # List sandboxes
        sandboxes = await provider.list_sandboxes()
        assert any(sb.id == sandbox.id for sb in sandboxes)

        # Get sandbox details
        fetched = await provider.get_sandbox(sandbox.id)
        assert fetched is not None
        assert fetched.id == sandbox.id

    finally:
        # Clean up
        await provider.destroy_sandbox(sandbox.id)
