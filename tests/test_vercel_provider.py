"""Tests for the Vercel sandbox provider."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import sandboxes.providers.vercel as vercel_module
from sandboxes.base import SandboxConfig
from sandboxes.exceptions import ProviderError
from sandboxes.providers.vercel import VercelProvider


def _make_sdk_sandbox(sandbox_id: str = "sb-vercel-123"):
    raw = SimpleNamespace(
        status="running",
        runtime="node22",
        region="iad1",
        timeout=120_000,
        memory=1024,
        vcpus=1,
        interactive_port=None,
        created_at=1_700_000_000_000,
    )

    finished_command = SimpleNamespace(
        exit_code=0,
        stdout=AsyncMock(return_value="hello\n"),
        stderr=AsyncMock(return_value=""),
    )

    async def iter_logs():
        yield SimpleNamespace(stream="stdout", data="line-1\n")
        yield SimpleNamespace(stream="stderr", data="line-2\n")

    detached_command = SimpleNamespace(
        wait=AsyncMock(return_value=finished_command),
        stdout=AsyncMock(return_value="hello\n"),
        stderr=AsyncMock(return_value=""),
        logs=iter_logs,
        kill=AsyncMock(),
    )

    return SimpleNamespace(
        sandbox_id=sandbox_id,
        sandbox=raw,
        routes=[{"port": 3000, "url": "https://example.vercel.run"}],
        run_command_detached=AsyncMock(return_value=detached_command),
        write_files=AsyncMock(),
        read_file=AsyncMock(return_value=b"downloaded-content"),
        stop=AsyncMock(),
        client=SimpleNamespace(aclose=AsyncMock()),
        _detached_command=detached_command,
    )


def _install_vercel_mocks(monkeypatch, sdk_sandbox):
    monkeypatch.setattr(vercel_module, "VERCEL_AVAILABLE", True)
    monkeypatch.setattr(
        vercel_module,
        "get_vercel_credentials",
        lambda **_kwargs: SimpleNamespace(
            token="token-test",
            project_id="project-test",
            team_id="team-test",
        ),
    )
    monkeypatch.setattr(
        vercel_module,
        "VercelSandbox",
        SimpleNamespace(
            create=AsyncMock(return_value=sdk_sandbox),
            get=AsyncMock(return_value=sdk_sandbox),
        ),
    )

    listed = SimpleNamespace(
        id=sdk_sandbox.sandbox_id,
        status="running",
        runtime="node22",
        region="iad1",
        timeout=120_000,
        memory=1024,
        vcpus=1,
        interactive_port=None,
        created_at=1_700_000_000_000,
    )

    class _MockClient:
        def __init__(self, **_kwargs):
            self.closed = False

        async def request_json(self, method: str, path: str, query: dict | None = None):
            assert method == "GET"
            assert path == "/v1/sandboxes"
            assert query and query["project"] == "project-test"
            return {"sandboxes": [{"id": sdk_sandbox.sandbox_id}]}

        async def aclose(self):
            self.closed = True

    class _MockSandboxesResponse:
        @staticmethod
        def model_validate(_data):
            return SimpleNamespace(sandboxes=[listed])

    monkeypatch.setattr(vercel_module, "AsyncAPIClient", _MockClient)
    monkeypatch.setattr(vercel_module, "SandboxesResponse", _MockSandboxesResponse)


@pytest.mark.asyncio
async def test_vercel_provider_happy_path(monkeypatch, tmp_path):
    """Create, list, execute, stream, upload, download, and destroy sandbox."""
    sdk_sandbox = _make_sdk_sandbox()
    _install_vercel_mocks(monkeypatch, sdk_sandbox)

    provider = VercelProvider(token="token", project_id="project", team_id="team")
    config = SandboxConfig(labels={"env": "test"}, env_vars={"BASE": "1"})

    sandbox = await provider.create_sandbox(config)
    assert sandbox.id == sdk_sandbox.sandbox_id
    assert sandbox.provider == "vercel"

    listed = await provider.list_sandboxes(labels={"env": "test"})
    assert len(listed) == 1
    assert listed[0].id == sdk_sandbox.sandbox_id

    result = await provider.execute_command(
        sandbox.id,
        "echo hello",
        env_vars={"RUNTIME": "yes"},
    )
    assert result.success
    assert "hello" in result.stdout
    sdk_sandbox.run_command_detached.assert_called()

    chunks = []
    async for chunk in provider.stream_execution(sandbox.id, "echo stream"):
        chunks.append(chunk)
    assert "line-1" in "".join(chunks)
    assert "[stderr]:" in "".join(chunks)

    upload_file = tmp_path / "upload.txt"
    upload_file.write_text("upload-content")
    uploaded = await provider.upload_file(sandbox.id, str(upload_file), "/workspace/upload.txt")
    assert uploaded is True
    sdk_sandbox.write_files.assert_called_once()

    download_file = tmp_path / "download.txt"
    downloaded = await provider.download_file(sandbox.id, "/workspace/file.txt", str(download_file))
    assert downloaded is True
    assert download_file.read_text() == "downloaded-content"

    destroyed = await provider.destroy_sandbox(sandbox.id)
    assert destroyed is True
    sdk_sandbox.stop.assert_called_once()


@pytest.mark.asyncio
async def test_vercel_execute_timeout(monkeypatch):
    """Timeout should kill detached command and return timed_out result."""
    sdk_sandbox = _make_sdk_sandbox()
    _install_vercel_mocks(monkeypatch, sdk_sandbox)

    async def _timeout():
        raise TimeoutError

    sdk_sandbox._detached_command.wait = AsyncMock(side_effect=_timeout)
    sdk_sandbox.run_command_detached = AsyncMock(return_value=sdk_sandbox._detached_command)

    provider = VercelProvider(token="token", project_id="project", team_id="team")
    sandbox = await provider.create_sandbox(SandboxConfig())
    result = await provider.execute_command(sandbox.id, "sleep 2", timeout=1)

    assert result.timed_out is True
    assert result.exit_code == -1
    sdk_sandbox._detached_command.kill.assert_called_once()


def test_vercel_missing_credentials(monkeypatch):
    """Provider should raise clear error when credentials are missing."""
    monkeypatch.setattr(vercel_module, "VERCEL_AVAILABLE", True)

    def _raise_credentials_error(**_kwargs):
        raise RuntimeError("missing credentials")

    monkeypatch.setattr(vercel_module, "get_vercel_credentials", _raise_credentials_error)

    with pytest.raises(ProviderError, match="Vercel credentials not provided"):
        VercelProvider()
