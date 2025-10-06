"""Tests for the Cloudflare sandbox provider."""

import json
import os

import httpx
import pytest

from sandboxes.base import SandboxConfig
from sandboxes.exceptions import SandboxError, SandboxNotFoundError
from sandboxes.providers.cloudflare import CloudflareProvider


@pytest.mark.asyncio
async def test_cloudflare_happy_path():
    """Create, execute, list, destroy, and health-check a Cloudflare sandbox."""
    responses = {
        ("POST", "/api/session/create"): httpx.Response(
            200,
            json={"success": True, "id": "test-session", "message": "created"},
        ),
        ("GET", "/api/session/list"): httpx.Response(
            200,
            json={"sessions": ["test-session"], "count": 1, "timestamp": "now"},
        ),
        ("POST", "/api/execute"): httpx.Response(
            200,
            json={
                "stdout": "hi\n",
                "stderr": "",
                "exitCode": 0,
                "success": True,
                "command": "echo hi",
            },
        ),
        ("DELETE", "/api/process/kill-all"): httpx.Response(
            200,
            json={"success": True, "killedCount": 0, "message": "done"},
        ),
        ("GET", "/api/ping"): httpx.Response(
            200,
            json={"message": "pong", "timestamp": "now"},
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        response = responses.get(key)
        if response is None:
            return httpx.Response(404)
        # echo request body for inspection in assertions if needed
        if request.url.path == "/api/execute":
            payload = json.loads(request.content.decode())
            assert payload["id"] == "test-session"
            assert "echo hi" in payload["command"]
        return response

    transport = httpx.MockTransport(handler)
    provider = CloudflareProvider(
        base_url="https://sandbox.example.workers.dev",
        api_token="token",
        transport=transport,
    )

    config = SandboxConfig(labels={"session": "test-session"})
    sandbox = await provider.create_sandbox(config)
    assert sandbox.id == "test-session"

    listed = await provider.list_sandboxes()
    assert any(sb.id == "test-session" for sb in listed)

    result = await provider.execute_command("test-session", "echo hi")
    assert result.success
    assert result.stdout == "hi\n"

    destroyed = await provider.destroy_sandbox("test-session")
    assert destroyed is True

    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_cloudflare_missing_session():
    """Executing against a missing session should raise SandboxNotFoundError."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/list":
            return httpx.Response(200, json={"sessions": [], "count": 0})
        return httpx.Response(404)

    provider = CloudflareProvider(
        base_url="https://sandbox.example.workers.dev",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SandboxNotFoundError):
        await provider.execute_command("unknown", "echo hi")


@pytest.mark.asyncio
async def test_cloudflare_http_error_raises_sandbox_error():
    """Non-2xx responses should surface as SandboxError."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/list":
            return httpx.Response(200, json={"sessions": ["bad"], "count": 1})
        if request.url.path == "/api/execute":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(404)

    provider = CloudflareProvider(
        base_url="https://sandbox.example.workers.dev",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SandboxError):
        await provider.execute_command("bad", "echo hi")


@pytest.mark.asyncio
async def test_cloudflare_stream_execution():
    """Test streaming execution with fallback to regular execution."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/list":
            return httpx.Response(200, json={"sessions": ["test-stream"], "count": 1})
        elif request.url.path == "/api/execute/stream":
            # Simulate SSE not available (404)
            return httpx.Response(404)
        elif request.url.path == "/api/execute":
            # Fallback to regular execution
            return httpx.Response(
                200,
                json={
                    "stdout": "streaming test output",
                    "stderr": "",
                    "exitCode": 0,
                    "success": True,
                },
            )
        return httpx.Response(404)

    provider = CloudflareProvider(
        base_url="https://sandbox.example.workers.dev",
        transport=httpx.MockTransport(handler),
    )

    chunks = []
    async for chunk in provider.stream_execution("test-stream", "echo test"):
        chunks.append(chunk)

    output = "".join(chunks)
    assert "streaming test output" in output


@pytest.mark.asyncio
async def test_cloudflare_execute_commands():
    """Test batch command execution."""
    command_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal command_count
        if request.url.path == "/api/session/list":
            return httpx.Response(200, json={"sessions": ["batch-test"], "count": 1})
        elif request.url.path == "/api/execute":
            command_count += 1
            return httpx.Response(
                200,
                json={
                    "stdout": f"command {command_count}",
                    "stderr": "",
                    "exitCode": 0,
                    "success": True,
                },
            )
        return httpx.Response(404)

    provider = CloudflareProvider(
        base_url="https://sandbox.example.workers.dev",
        transport=httpx.MockTransport(handler),
    )

    results = await provider.execute_commands("batch-test", ["echo one", "echo two", "echo three"])

    assert len(results) == 3
    assert all(r.success for r in results)
    assert results[0].stdout == "command 1"
    assert results[1].stdout == "command 2"
    assert results[2].stdout == "command 3"


@pytest.mark.asyncio
async def test_cloudflare_get_or_create_sandbox():
    """Test get_or_create_sandbox functionality."""
    create_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_called
        if request.url.path == "/api/session/list":
            # First call returns existing, second returns empty
            if not create_called:
                return httpx.Response(200, json={"sessions": ["existing"], "count": 1})
            return httpx.Response(200, json={"sessions": [], "count": 0})
        elif request.url.path == "/api/session/create":
            create_called = True
            return httpx.Response(200, json={"success": True, "id": "new-sandbox"})
        return httpx.Response(404)

    provider = CloudflareProvider(
        base_url="https://sandbox.example.workers.dev",
        transport=httpx.MockTransport(handler),
    )

    # Should find existing sandbox
    config1 = SandboxConfig(labels={"session": "existing"})
    sandbox1 = await provider.get_or_create_sandbox(config1)
    assert sandbox1.id == "existing"
    assert not create_called

    # Should create new sandbox
    config2 = SandboxConfig(labels={"session": "new"})
    sandbox2 = await provider.get_or_create_sandbox(config2)
    assert sandbox2.id == "new"  # The provider uses the session label as ID
    assert create_called


@pytest.mark.asyncio
async def test_cloudflare_file_operations():
    """Test upload and download file with fallback."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session/list":
            return httpx.Response(200, json={"sessions": ["file-test"], "count": 1})
        elif request.url.path == "/api/file/write":
            # Simulate file write endpoint not available
            return httpx.Response(404)
        elif request.url.path == "/api/file/read":
            # Simulate file read endpoint not available
            return httpx.Response(404)
        elif request.url.path == "/api/execute":
            payload = json.loads(request.content.decode())
            command = payload["command"]

            # Handle mkdir command
            if "mkdir -p" in command or "base64 -d" in command:
                return httpx.Response(
                    200, json={"stdout": "", "stderr": "", "exitCode": 0, "success": True}
                )
            # Handle base64 read command
            elif "cat" in command and "base64" in command:
                # Return base64 encoded content
                import base64

                test_content = base64.b64encode(b"test file content").decode("utf-8")
                return httpx.Response(
                    200, json={"stdout": test_content, "stderr": "", "exitCode": 0, "success": True}
                )
        return httpx.Response(404)

    provider = CloudflareProvider(
        base_url="https://sandbox.example.workers.dev",
        transport=httpx.MockTransport(handler),
    )

    # Test upload (will use fallback)
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content")
        temp_path = f.name

    try:
        success = await provider.upload_file("file-test", temp_path, "/workspace/test.txt")
        assert success
    finally:
        os.unlink(temp_path)

    # Test download (will use fallback)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        output_path = f.name

    try:
        success = await provider.download_file("file-test", "/workspace/test.txt", output_path)
        assert success
        with open(output_path, "rb") as f:
            content = f.read()
        assert content == b"test file content"
    finally:
        os.unlink(output_path)


@pytest.mark.asyncio
@pytest.mark.cloudflare
async def test_cloudflare_live_integration():
    base_url = os.getenv("CLOUDFLARE_SANDBOX_BASE_URL")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")

    if not base_url or not api_token:
        pytest.skip("Cloudflare live credentials not configured")

    provider = CloudflareProvider(base_url=base_url, api_token=api_token)

    config = SandboxConfig(labels={"session": "pytest-live"})
    sandbox = await provider.create_sandbox(config)

    try:
        result = await provider.execute_command(sandbox.id, "echo cloudflare")
        assert result.success
        assert "cloudflare" in result.stdout

        # Listing should include our session
        sessions = await provider.list_sandboxes()
        assert any(sb.id == sandbox.id for sb in sessions)

        assert await provider.health_check() is True
    finally:
        await provider.destroy_sandbox(sandbox.id)
