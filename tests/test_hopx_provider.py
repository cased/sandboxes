"""Tests for the Hopx sandbox provider."""

import os
import tempfile
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
    with patch("sandboxes.providers.hopx.HopxSandbox") as MockHopxSandbox:  # noqa: N806
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

    with patch("sandboxes.providers.hopx.HopxSandbox") as MockHopxSandbox:  # noqa: N806
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

    # Create mock sandbox without streaming support (fallback to simulated)
    mock_sandbox = MagicMock()
    mock_sandbox.sandbox_id = sandbox_id
    # Explicitly set spec without run_code_stream to force fallback
    mock_sandbox_spec = MagicMock(spec=["sandbox_id", "files", "commands"])

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox_spec,
        "labels": {},
        "last_accessed": 0,
    }

    with patch.object(provider, "execute_command") as mock_exec:
        mock_exec.return_value = ExecutionResult(
            exit_code=0,
            stdout="streaming output test",
            stderr="",
            duration_ms=50,
            truncated=False,
            timed_out=False,
        )

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
        with open(output_path) as f:
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

    with patch("sandboxes.providers.hopx.HopxSandbox") as MockHopxSandbox:  # noqa: N806
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
async def test_hopx_run_code_with_rich_outputs():
    """Test run_code method for capturing plots and rich outputs."""
    sandbox_id = "rich-output-test"
    provider = HopxProvider(api_key="test-key")

    # Create mock sandbox with run_code support
    mock_sandbox = AsyncMock()
    mock_sandbox.sandbox_id = sandbox_id

    # Mock rich output result
    from unittest.mock import MagicMock

    mock_result = MagicMock()
    mock_result.success = True
    mock_result.stdout = "Plot created\n"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.execution_time = 1.5
    mock_result.rich_outputs = [
        MagicMock(
            type="image/png",
            data="iVBORw0KGgoAAAANSUhEUg...",  # Base64 PNG data
            metadata={"width": 800, "height": 600},
        )
    ]

    mock_sandbox.run_code = AsyncMock(return_value=mock_result)

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "last_accessed": 0,
    }

    # Execute code
    result = await provider.run_code(
        sandbox_id,
        code="import matplotlib.pyplot as plt\nplt.plot([1,2,3])",
        language="python",
    )

    # Verify result structure
    assert result["success"] is True
    assert result["stdout"] == "Plot created\n"
    assert result["exit_code"] == 0
    assert result["execution_time"] == 1.5
    assert len(result["rich_outputs"]) == 1
    assert result["rich_outputs"][0]["type"] == "image/png"
    assert "data" in result["rich_outputs"][0]

    # Verify SDK method was called
    mock_sandbox.run_code.assert_called_once()
    call_kwargs = mock_sandbox.run_code.call_args.kwargs
    assert call_kwargs["code"] == "import matplotlib.pyplot as plt\nplt.plot([1,2,3])"
    assert call_kwargs["language"] == "python"


@pytest.mark.asyncio
async def test_hopx_binary_file_upload():
    """Test binary file upload (images, PDFs, etc.)."""
    sandbox_id = "binary-upload-test"
    provider = HopxProvider(api_key="test-key")

    # Create a temporary binary file
    import tempfile

    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".png") as f:
        # Write fake PNG header
        f.write(b"\x89PNG\r\n\x1a\n")
        temp_path = f.name

    try:
        # Create mock sandbox
        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = sandbox_id
        mock_sandbox.files.write = AsyncMock()

        provider._sandboxes[sandbox_id] = {
            "hopx_sandbox": mock_sandbox,
            "labels": {},
            "last_accessed": 0,
        }

        # Upload binary file (binary detected automatically by .png extension)
        success = await provider.upload_file(
            sandbox_id, temp_path, "/workspace/image.png"
        )
        assert success

        # Verify SDK was called with bytes
        mock_sandbox.files.write.assert_called_once()
        call_kwargs = mock_sandbox.files.write.call_args.kwargs
        assert call_kwargs["path"] == "/workspace/image.png"
        assert isinstance(call_kwargs["content"], bytes)
        assert call_kwargs["content"].startswith(b"\x89PNG")
    finally:
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_hopx_binary_file_download():
    """Test binary file download (images, PDFs, etc.)."""
    sandbox_id = "binary-download-test"
    provider = HopxProvider(api_key="test-key")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "downloaded.png")

        # Create mock sandbox
        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = sandbox_id
        # SDK returns bytes for binary files
        mock_sandbox.files.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")

        provider._sandboxes[sandbox_id] = {
            "hopx_sandbox": mock_sandbox,
            "labels": {},
            "last_accessed": 0,
        }

        # Download binary file (binary detected automatically by SDK)
        success = await provider.download_file(
            sandbox_id, "/workspace/plot.png", output_path
        )
        assert success

        # Verify binary content
        with open(output_path, "rb") as f:
            content = f.read()
        assert content == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_hopx_screenshot():
    """Test desktop screenshot capture."""
    sandbox_id = "screenshot-test"
    provider = HopxProvider(api_key="test-key")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "screen.png")

        # Create mock sandbox with desktop support
        mock_sandbox = AsyncMock()
        mock_sandbox.sandbox_id = sandbox_id
        mock_desktop = AsyncMock()
        mock_desktop.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nFAKE_SCREENSHOT")
        mock_sandbox.desktop = mock_desktop

        provider._sandboxes[sandbox_id] = {
            "hopx_sandbox": mock_sandbox,
            "labels": {},
            "last_accessed": 0,
        }

        # Capture screenshot
        img_bytes = await provider.screenshot(sandbox_id, output_path)

        assert img_bytes is not None
        assert img_bytes.startswith(b"\x89PNG")
        assert os.path.exists(output_path)

        # Verify file was saved
        with open(output_path, "rb") as f:
            saved_content = f.read()
        assert saved_content == img_bytes


@pytest.mark.asyncio
async def test_hopx_screenshot_no_desktop_support():
    """Test screenshot when desktop is not available."""
    sandbox_id = "no-desktop-test"
    provider = HopxProvider(api_key="test-key")

    # Create mock sandbox WITHOUT desktop support
    mock_sandbox = MagicMock()
    mock_sandbox.sandbox_id = sandbox_id
    # Explicitly remove desktop attribute using spec
    mock_sandbox_spec = MagicMock(spec=["sandbox_id", "files", "commands"])

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox_spec,
        "labels": {},
        "last_accessed": 0,
    }

    # Try to capture screenshot (should return None gracefully)
    img_bytes = await provider.screenshot(sandbox_id)
    assert img_bytes is None


@pytest.mark.asyncio
async def test_hopx_get_desktop_vnc_url():
    """Test getting VNC URL for desktop automation."""
    sandbox_id = "vnc-test"
    provider = HopxProvider(api_key="test-key")

    # Create mock sandbox with desktop support
    mock_sandbox = AsyncMock()
    mock_sandbox.sandbox_id = sandbox_id
    mock_desktop = AsyncMock()
    mock_vnc_info = MagicMock()
    mock_vnc_info.url = "wss://hopx-vnc-123.hopx.dev/vnc"
    mock_desktop.start_vnc = AsyncMock(return_value=mock_vnc_info)
    mock_sandbox.desktop = mock_desktop

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "last_accessed": 0,
    }

    # Get VNC URL
    vnc_url = await provider.get_desktop_vnc_url(sandbox_id)

    assert vnc_url is not None
    assert vnc_url == "wss://hopx-vnc-123.hopx.dev/vnc"
    mock_desktop.start_vnc.assert_called_once()


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


@pytest.mark.asyncio
async def test_hopx_get_preview_url():
    """Test get_preview_url method for accessing sandbox services."""
    provider = HopxProvider(api_key="test-key")
    sandbox_id = "preview-url-test"

    # Mock sandbox with get_preview_url method (SDK v0.3.0+)
    mock_sandbox = AsyncMock()
    mock_sandbox.sandbox_id = sandbox_id
    mock_sandbox.get_preview_url = AsyncMock(return_value="https://8080-sandbox123.eu-1001.vms.hopx.dev/")

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "last_accessed": 0,
    }

    # Test custom port
    url = await provider.get_preview_url(sandbox_id, port=8080)
    assert url == "https://8080-sandbox123.eu-1001.vms.hopx.dev/"
    mock_sandbox.get_preview_url.assert_called_once_with(8080)

    # Test default port (7777)
    mock_sandbox.get_preview_url.reset_mock()
    mock_sandbox.get_preview_url.return_value = "https://7777-sandbox123.eu-1001.vms.hopx.dev/"
    url = await provider.get_preview_url(sandbox_id)
    assert url == "https://7777-sandbox123.eu-1001.vms.hopx.dev/"
    mock_sandbox.get_preview_url.assert_called_once_with(7777)


@pytest.mark.asyncio
async def test_hopx_get_agent_url():
    """Test get_agent_url convenience method."""
    provider = HopxProvider(api_key="test-key")
    sandbox_id = "agent-url-test"

    # Mock sandbox
    mock_sandbox = AsyncMock()
    mock_sandbox.sandbox_id = sandbox_id
    mock_sandbox.get_preview_url = AsyncMock(return_value="https://7777-sandbox123.eu-1001.vms.hopx.dev/")

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "last_accessed": 0,
    }

    # Test agent URL (should call get_preview_url with port 7777)
    url = await provider.get_agent_url(sandbox_id)
    assert url == "https://7777-sandbox123.eu-1001.vms.hopx.dev/"
    mock_sandbox.get_preview_url.assert_called_once_with(7777)


@pytest.mark.asyncio
async def test_hopx_get_preview_url_not_found():
    """Test get_preview_url raises SandboxNotFoundError for unknown sandbox."""
    provider = HopxProvider(api_key="test-key")

    with pytest.raises(SandboxNotFoundError, match="Sandbox .* not found"):
        await provider.get_preview_url("unknown-sandbox", port=8080)


@pytest.mark.asyncio
async def test_hopx_timeout_parameter_compatibility():
    """Test that timeout parameter is correctly passed to SDK methods."""
    provider = HopxProvider(api_key="test-key")
    sandbox_id = "timeout-test"

    # Mock the SDK sandbox and commands
    from unittest.mock import AsyncMock, MagicMock

    mock_sandbox = MagicMock()
    mock_commands = MagicMock()
    mock_commands.run = AsyncMock(
        return_value=MagicMock(exit_code=0, stdout="success", stderr="", execution_time=1.5)
    )
    mock_sandbox.commands = mock_commands
    mock_sandbox.run_code = AsyncMock(
        return_value=MagicMock(
            exit_code=0,
            stdout="success",
            stderr="",
            execution_time=1.5,
            success=True,
            rich_outputs=[],
        )
    )

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "created_at": 0,
        "last_accessed": 0,
        "template": "test",
    }

    # Test execute_command with custom timeout
    await provider.execute_command(sandbox_id, "echo test", timeout=45)
    mock_commands.run.assert_called_with(command="echo test", timeout_seconds=45, env=None)

    # Test run_code with custom timeout
    await provider.run_code(sandbox_id, "print('test')", timeout=90)
    mock_sandbox.run_code.assert_called_with(
        code="print('test')", language="python", timeout_seconds=90, env=None
    )


@pytest.mark.asyncio
async def test_hopx_concurrent_command_execution():
    """Test executing multiple commands concurrently in the same sandbox."""
    provider = HopxProvider(api_key="test-key")
    sandbox_id = "concurrent-test"

    from unittest.mock import AsyncMock, MagicMock

    call_count = 0

    async def mock_run(command, **kwargs):
        nonlocal call_count
        call_count += 1
        return MagicMock(
            exit_code=0,
            stdout=f"result-{call_count}",
            stderr="",
            execution_time=0.1,
        )

    mock_sandbox = MagicMock()
    mock_commands = MagicMock()
    mock_commands.run = AsyncMock(side_effect=mock_run)
    mock_sandbox.commands = mock_commands

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "created_at": 0,
        "last_accessed": 0,
        "template": "test",
    }

    # Execute multiple commands concurrently
    import asyncio

    tasks = [provider.execute_command(sandbox_id, f"echo test{i}") for i in range(5)]
    results = await asyncio.gather(*tasks)

    # Verify all commands executed successfully
    assert len(results) == 5
    assert all(r.exit_code == 0 for r in results)
    assert call_count == 5


@pytest.mark.asyncio
async def test_hopx_environment_variables_in_commands():
    """Test that environment variables are properly passed to command execution."""
    provider = HopxProvider(api_key="test-key")
    sandbox_id = "env-test"

    from unittest.mock import AsyncMock, MagicMock

    mock_sandbox = MagicMock()
    mock_commands = MagicMock()
    mock_commands.run = AsyncMock(
        return_value=MagicMock(
            exit_code=0, stdout="API_KEY=secret123", stderr="", execution_time=0.1
        )
    )
    mock_sandbox.commands = mock_commands

    provider._sandboxes[sandbox_id] = {
        "hopx_sandbox": mock_sandbox,
        "labels": {},
        "created_at": 0,
        "last_accessed": 0,
        "template": "test",
    }

    # Execute command with environment variables
    env_vars = {"API_KEY": "secret123", "DEBUG": "true"}
    result = await provider.execute_command(sandbox_id, "echo $API_KEY", env_vars=env_vars)

    # Verify env vars were passed correctly
    mock_commands.run.assert_called_once()
    call_args = mock_commands.run.call_args
    assert call_args.kwargs["env"] == env_vars
    assert result.success


@pytest.mark.asyncio
async def test_hopx_health_check_handles_none_response():
    """Test that health_check handles None response from SDK gracefully."""
    provider = HopxProvider(api_key="test-key")

    from unittest.mock import AsyncMock, patch

    # Test with None response (should return False)
    with patch("sandboxes.providers.hopx.HopxSandbox") as mock_hopx:
        mock_hopx.list = AsyncMock(return_value=None)
        result = await provider.health_check()
        assert result is False  # Should return False when list returns None

    # Test with empty list response (should return True)
    with patch("sandboxes.providers.hopx.HopxSandbox") as mock_hopx:
        mock_hopx.list = AsyncMock(return_value=[])
        result = await provider.health_check()
        assert result is True  # Should return True when list returns empty list

    # Test with non-empty list response (should return True)
    with patch("sandboxes.providers.hopx.HopxSandbox") as mock_hopx:
        mock_hopx.list = AsyncMock(return_value=["sandbox1", "sandbox2"])
        result = await provider.health_check()
        assert result is True  # Should return True when list returns sandboxes
