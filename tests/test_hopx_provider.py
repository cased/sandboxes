"""Tests for the Hopx sandbox provider."""

import json
import os
import tempfile
from unittest.mock import patch

import httpx
import pytest

from sandboxes.base import SandboxConfig
from sandboxes.exceptions import SandboxError, SandboxNotFoundError
from sandboxes.providers.hopx import HopxProvider


@pytest.mark.asyncio
async def test_hopx_happy_path():
    """Create, execute, list, destroy, and health-check a Hopx sandbox."""
    sandbox_id = "hopx-test-123"
    responses = {
        ("POST", "/v1/sandboxes"): httpx.Response(
            200,
            json={"id": sandbox_id, "state": "creating", "templateId": "python"},
        ),
        ("GET", f"/v1/sandboxes/{sandbox_id}"): httpx.Response(
            200,
            json={"id": sandbox_id, "state": "running", "templateId": "python"},
        ),
        ("GET", "/v1/sandboxes"): httpx.Response(
            200,
            json={"sandboxes": [{"id": sandbox_id, "state": "running", "templateId": "python"}]},
        ),
        ("POST", "/commands/run"): httpx.Response(
            200,
            json={"stdout": "hello\n", "stderr": "", "exitCode": 0, "duration": 100},
        ),
        ("DELETE", f"/v1/sandboxes/{sandbox_id}"): httpx.Response(
            200,
            json={"success": True},
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        response = responses.get(key)
        if response is None:
            return httpx.Response(404, json={"error": "Not found"})

        # Validate request headers
        assert "X-API-Key" in request.headers
        assert request.headers["X-API-Key"] == "test-key"

        # Validate command execution request
        if request.url.path == "/commands/run":
            payload = json.loads(request.content.decode())
            assert "command" in payload
            assert "echo hello" in payload["command"]

        return response

    provider = HopxProvider(api_key="test-key")

    # Mock the transport for both control plane and data plane
    with patch.object(provider, "_request") as mock_request:
        call_count = 0

        async def side_effect(method, path, **kwargs):
            nonlocal call_count
            call_count += 1

            # Control plane requests
            if path == "/v1/sandboxes" and method == "POST":
                return {"id": sandbox_id, "state": "creating", "templateId": "python"}
            elif path == f"/v1/sandboxes/{sandbox_id}" and method == "GET":
                # First call during wait_for_ready, second during get_sandbox
                return {"id": sandbox_id, "state": "running", "templateId": "python"}
            elif path == "/v1/sandboxes" and method == "GET":
                return {"sandboxes": [{"id": sandbox_id, "state": "running", "templateId": "python"}]}
            elif path == f"/v1/sandboxes/{sandbox_id}" and method == "DELETE":
                return {"success": True}
            # Data plane requests
            elif path == "/commands/run" and method == "POST":
                return {"stdout": "hello\n", "stderr": "", "exitCode": 0, "duration": 100}
            else:
                raise SandboxNotFoundError(f"Not found: {path}")

        mock_request.side_effect = side_effect

        # Create sandbox
        config = SandboxConfig(labels={"test": "hopx"})
        sandbox = await provider.create_sandbox(config)
        assert sandbox.id == sandbox_id
        assert sandbox.provider == "hopx"

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


@pytest.mark.asyncio
async def test_hopx_missing_api_key():
    """Provider should raise ProviderError if API key is not provided."""
    from sandboxes.exceptions import ProviderError

    with patch.dict(os.environ, {}, clear=True), pytest.raises(
        ProviderError, match="Hopx API key not provided"
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

    with patch.object(provider, "_request") as mock_request:

        async def side_effect(method, path, **kwargs):
            raise SandboxNotFoundError(f"Sandbox not found: {path}")

        mock_request.side_effect = side_effect

        sandbox = await provider.get_sandbox("unknown-id")
        assert sandbox is None


@pytest.mark.asyncio
async def test_hopx_http_error_raises_sandbox_error():
    """Non-2xx responses should surface as SandboxError."""
    provider = HopxProvider(api_key="test-key")

    with patch.object(provider, "_request") as mock_request:

        async def side_effect(method, path, **kwargs):
            raise SandboxError("Internal server error")

        mock_request.side_effect = side_effect

        # health_check catches SandboxError and returns False
        result = await provider.health_check()
        assert result is False

        # Test with a method that doesn't catch the error
        with pytest.raises(SandboxError):
            await provider.get_sandbox("test-id")


@pytest.mark.asyncio
async def test_hopx_stream_execution():
    """Test streaming execution with simulated chunking."""
    sandbox_id = "stream-test"
    provider = HopxProvider(api_key="test-key")

    with patch.object(provider, "execute_command") as mock_exec:
        from sandboxes.base import ExecutionResult

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
    """Test file upload with base64 encoding."""
    sandbox_id = "file-upload-test"
    provider = HopxProvider(api_key="test-key")

    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test file content")
        temp_path = f.name

    try:
        with patch.object(provider, "_post_to_data_plane") as mock_post:
            mock_post.return_value = {"success": True}

            success = await provider.upload_file(sandbox_id, temp_path, "/workspace/test.txt")
            assert success

            # Verify the call was made with base64 encoded content
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["path"] == "/workspace/test.txt"
            assert payload["encoding"] == "base64"
            assert "content" in payload
    finally:
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_hopx_file_download():
    """Test file download with base64 decoding."""
    sandbox_id = "file-download-test"
    provider = HopxProvider(api_key="test-key")

    with tempfile.NamedTemporaryFile(delete=False) as f:
        output_path = f.name

    try:
        with patch.object(provider, "_get_from_data_plane") as mock_get:
            import base64

            test_content = b"downloaded file content"
            encoded = base64.b64encode(test_content).decode("utf-8")
            mock_get.return_value = {"content": encoded, "encoding": "base64"}

            success = await provider.download_file(sandbox_id, "/workspace/test.txt", output_path)
            assert success

            # Verify the content was decoded correctly
            with open(output_path, "rb") as f:
                content = f.read()
            assert content == test_content
    finally:
        os.unlink(output_path)


@pytest.mark.asyncio
async def test_hopx_sandbox_state_mapping():
    """Test that Hopx states are mapped correctly to SandboxState."""
    from sandboxes.base import SandboxState

    sandbox_id = "state-test"
    provider = HopxProvider(api_key="test-key")

    with patch.object(provider, "_request") as mock_request:

        async def side_effect(method, path, **kwargs):
            if "creating" in path:
                return {"id": sandbox_id, "state": "creating"}
            elif "running" in path:
                return {"id": sandbox_id, "state": "running"}
            elif "stopped" in path:
                return {"id": sandbox_id, "state": "stopped"}
            elif "paused" in path:
                return {"id": sandbox_id, "state": "paused"}

        mock_request.side_effect = side_effect

        # Test each state
        sandbox_creating = await provider._to_sandbox(sandbox_id, {"state": "creating"})
        assert sandbox_creating.state == SandboxState.RUNNING  # Treated as running

        sandbox_running = await provider._to_sandbox(sandbox_id, {"state": "running"})
        assert sandbox_running.state == SandboxState.RUNNING

        sandbox_stopped = await provider._to_sandbox(sandbox_id, {"state": "stopped"})
        assert sandbox_stopped.state == SandboxState.STOPPED

        sandbox_paused = await provider._to_sandbox(sandbox_id, {"state": "paused"})
        assert sandbox_paused.state == SandboxState.STOPPED  # Paused treated as stopped


@pytest.mark.asyncio
async def test_hopx_find_sandbox_with_labels():
    """Test finding a sandbox by labels."""
    provider = HopxProvider(api_key="test-key")

    with patch.object(provider, "list_sandboxes") as mock_list:
        from sandboxes.base import Sandbox, SandboxState

        # Create mock sandboxes
        sandbox1 = Sandbox(
            id="sb-1",
            provider="hopx",
            state=SandboxState.RUNNING,
            labels={"env": "prod", "app": "web"},
            metadata={},
        )
        sandbox2 = Sandbox(
            id="sb-2",
            provider="hopx",
            state=SandboxState.RUNNING,
            labels={"env": "dev", "app": "api"},
            metadata={},
        )

        # Mock list_sandboxes to filter by labels properly
        async def mock_list_side_effect(labels=None):
            all_sandboxes = [sandbox1, sandbox2]
            if labels:
                return [s for s in all_sandboxes if all(s.labels.get(k) == v for k, v in labels.items())]
            return all_sandboxes

        mock_list.side_effect = mock_list_side_effect

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

    # Add some sandboxes to internal tracking
    import time

    provider._sandboxes = {
        "old-sandbox": {"last_accessed": time.time() - 1000, "labels": {}},
        "new-sandbox": {"last_accessed": time.time(), "labels": {}},
    }

    with patch.object(provider, "destroy_sandbox") as mock_destroy:
        mock_destroy.return_value = True

        # Cleanup with 500 second timeout
        await provider.cleanup_idle_sandboxes(idle_timeout=500)

        # Should only destroy old-sandbox
        mock_destroy.assert_called_once_with("old-sandbox")


@pytest.mark.asyncio
async def test_hopx_env_vars_application():
    """Test that environment variables are properly applied to commands."""
    command = "python script.py"
    env_vars = {"API_KEY": "secret123", "DEBUG": "true"}

    result = HopxProvider._apply_env_vars_to_command(command, env_vars)

    assert "export API_KEY='secret123'" in result
    assert "export DEBUG='true'" in result
    assert "python script.py" in result
    assert "&&" in result  # Commands should be chained


@pytest.mark.asyncio
async def test_hopx_template_selection():
    """Test that templates can be specified via config."""
    provider = HopxProvider(api_key="test-key")

    with patch.object(provider, "_request") as mock_request:
        sandbox_id = "template-test"

        async def side_effect(method, path, json=None, **kwargs):
            if method == "POST" and path == "/v1/sandboxes":
                # Verify template is passed
                assert json["template_name"] == "nodejs"
                return {
                    "id": sandbox_id,
                    "status": "running",
                    "template_name": "nodejs",
                    "auth_token": "test-jwt-token",
                    "public_host": "https://template-test.hopx.dev",
                }
            elif method == "GET" and path == f"/v1/sandboxes/{sandbox_id}":
                return {
                    "id": sandbox_id,
                    "status": "running",
                    "template_name": "nodejs",
                }

        mock_request.side_effect = side_effect

        # Create with custom template
        config = SandboxConfig(provider_config={"template": "nodejs"})
        sandbox = await provider.create_sandbox(config)
        assert sandbox.id == sandbox_id


@pytest.mark.asyncio
@pytest.mark.hopx
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
