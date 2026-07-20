"""Tests for the Tenki sandbox provider.

The provider is a thin adapter over the official ``tenki-sandbox`` async SDK,
so these tests inject a fake ``AsyncClient`` (via ``client=``) that mimics the
slice of the SDK surface the provider touches. The provider classifies SDK
errors by class name, so the fakes raise exceptions named exactly like the
SDK's (``SessionNotFoundError`` etc.).
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandboxes.base import SandboxConfig, SandboxState
from sandboxes.exceptions import (
    ProviderError,
    SandboxAuthenticationError,
    SandboxError,
    SandboxNotFoundError,
    SandboxTimeoutError,
)
from sandboxes.providers.tenki import TenkiProvider


# --------------------------------------------------------------------------
# SDK-lookalike exceptions (matched by class name inside the provider)
# --------------------------------------------------------------------------
class SessionNotFoundError(Exception):
    pass


class UnauthorizedError(Exception):
    pass


class CommandTimeoutError(Exception):
    pass


# --------------------------------------------------------------------------
# Fakes mimicking tenki_sandbox.AsyncClient / AsyncSandbox
# --------------------------------------------------------------------------
def _info(sandbox_id="sbx-123", state="RUNNING", metadata=None):
    return SimpleNamespace(
        id=sandbox_id,
        name="demo",
        state=state,
        project_id="proj-1",
        workspace_id="ws-1",
        cpu_cores=2,
        memory_mb=4096,
        disk_size_gb=10,
        metadata=metadata or {},
        tags=(),
        sticky=False,
        runtime_state="UNSPECIFIED",
    )


def _result(exit_code=0, stdout=b"", stderr=b"", duration_ms=None, reason=None):
    return SimpleNamespace(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        reason=reason,
        stdout_text=stdout.decode(errors="replace"),
        stderr_text=stderr.decode(errors="replace"),
    )


class _AsyncChunks:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk


class FakeFS:
    def __init__(self):
        self.uploads = []
        self.downloads = []

    async def upload(self, local_path, remote_path, *, chunk_bytes=1024 * 1024):
        self.uploads.append((local_path, remote_path))

    async def download(self, remote_path, local_path):
        self.downloads.append((remote_path, local_path))
        Path(local_path).write_bytes(b"downloaded")


class FakeProcess:
    def __init__(self, stdout_chunks, stderr=b"", wait_error=None, wait_hang=False):
        self.stdout = _AsyncChunks(stdout_chunks)
        self._result = _result(stderr=stderr)
        self._wait_error = wait_error
        self._wait_hang = wait_hang
        self.killed = False

    async def close_stdin(self):
        pass

    async def wait(self, timeout=None):
        if self._wait_hang:
            await asyncio.sleep(5)
        if self._wait_error is not None:
            raise self._wait_error
        return self._result

    async def kill(self):
        self.killed = True


class FakeSandbox:
    def __init__(self, info, *, shell_result=None, shell_error=None, process=None):
        self._info = info
        self.fs = FakeFS()
        self._shell_result = shell_result if shell_result is not None else _result()
        self._shell_error = shell_error
        self._process = process
        self.closed = False
        self.shell_calls = []

    @property
    def id(self):
        return self._info.id

    @property
    def info(self):
        return self._info

    async def shell(self, command, timeout=None, env=None):
        self.shell_calls.append((command, timeout, env))
        if self._shell_error is not None:
            raise self._shell_error
        return self._shell_result

    async def start(self, *argv, env=None, timeout=None):
        return self._process

    async def close_if_open(self):
        self.closed = True


class FakeClient:
    def __init__(
        self,
        *,
        sandboxes=None,
        create_result=None,
        get_error=None,
        list_result=None,
        whoami_error=None,
    ):
        self.sandboxes = sandboxes or {}
        self.create_result = create_result
        self.get_error = get_error
        self.list_result = list_result if list_result is not None else []
        self.whoami_error = whoami_error
        self.create_calls = []
        self.closed = False

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.create_result

    async def get(self, sandbox_id):
        if self.get_error is not None:
            raise self.get_error
        if sandbox_id in self.sandboxes:
            return self.sandboxes[sandbox_id]
        raise SessionNotFoundError(sandbox_id)

    async def list(self):
        return list(self.list_result)

    async def list_project(self, project_id):
        return list(self.list_result)

    async def who_am_i(self):
        if self.whoami_error is not None:
            raise self.whoami_error
        return SimpleNamespace(owner_id="u", owner_type="USER")

    async def close(self):
        self.closed = True


def _provider(client, **kwargs) -> TenkiProvider:
    return TenkiProvider(client=client, **kwargs)


# --------------------------------------------------------------------------
# Construction / auth
# --------------------------------------------------------------------------
def test_requires_api_key(monkeypatch):
    """Constructing without a key/client (and without env) should fail."""
    monkeypatch.delenv("TENKI_API_KEY", raising=False)
    monkeypatch.delenv("TENKI_AUTH_TOKEN", raising=False)
    with pytest.raises(ProviderError):
        TenkiProvider()


def test_reads_api_key_from_env(monkeypatch):
    """The API key should be picked up from the environment."""
    monkeypatch.setenv("TENKI_API_KEY", "tk-env-key")
    provider = TenkiProvider()
    assert provider.api_key == "tk-env-key"
    assert provider.name == "tenki"


# --------------------------------------------------------------------------
# Lifecycle + execution
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenki_happy_path():
    """Create, get, list, execute, and destroy a Tenki sandbox."""
    sb = FakeSandbox(
        _info("sbx-123", metadata={"task": "demo"}),
        shell_result=_result(exit_code=0, stdout=b"hi\n", duration_ms=12),
    )
    client = FakeClient(create_result=sb, sandboxes={"sbx-123": sb}, list_result=[sb])
    provider = _provider(client)

    config = SandboxConfig(
        image="python",
        env_vars={"FOO": "bar"},
        labels={"task": "demo"},
        memory_mb=4096,
        cpu_cores=2,
    )
    sandbox = await provider.create_sandbox(config)
    assert sandbox.id == "sbx-123"
    assert sandbox.state == SandboxState.RUNNING
    assert sandbox.provider == "tenki"

    # create() should forward the mapped fields to the SDK.
    call = client.create_calls[0]
    assert call["image"] == "python"
    assert call["env"] == {"FOO": "bar"}
    assert call["metadata"] == {"task": "demo"}
    assert call["memory_mb"] == 4096
    assert call["cpu_cores"] == 2

    fetched = await provider.get_sandbox("sbx-123")
    assert fetched is not None and fetched.id == "sbx-123"

    listed = await provider.list_sandboxes()
    assert any(s.id == "sbx-123" for s in listed)

    result = await provider.execute_command("sbx-123", "echo hi")
    assert result.success
    assert result.stdout == "hi\n"
    assert result.duration_ms == 12
    assert sb.shell_calls[-1][0] == "echo hi"

    assert await provider.destroy_sandbox("sbx-123") is True
    assert sb.closed is True
    assert "sbx-123" not in provider._sandboxes  # noqa: SLF001 - intentional probe


@pytest.mark.asyncio
async def test_get_sandbox_missing_returns_none():
    """get_sandbox should return None when the session does not exist."""
    provider = _provider(FakeClient())
    assert await provider.get_sandbox("nope") is None


@pytest.mark.asyncio
async def test_destroy_missing_returns_false():
    """destroy_sandbox should return False when the sandbox is gone."""
    provider = _provider(FakeClient())
    assert await provider.destroy_sandbox("gone") is False


@pytest.mark.asyncio
async def test_authentication_error():
    """An SDK UnauthorizedError should surface as SandboxAuthenticationError."""
    provider = _provider(FakeClient(get_error=UnauthorizedError("bad key")))
    with pytest.raises(SandboxAuthenticationError):
        await provider.execute_command("sbx-1", "echo hi")


@pytest.mark.asyncio
async def test_generic_error_raises_sandbox_error():
    """An unexpected SDK error should surface as SandboxError."""
    sb = FakeSandbox(_info("sbx-1"), shell_error=RuntimeError("boom"))
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))
    with pytest.raises(SandboxError, match="boom"):
        await provider.execute_command("sbx-1", "echo hi")


@pytest.mark.asyncio
async def test_execute_command_timeout():
    """A command timeout is reported as a timed-out result, not an exception."""
    sb = FakeSandbox(_info("sbx-1"), shell_error=CommandTimeoutError("deadline"))
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))
    result = await provider.execute_command("sbx-1", "sleep 999", timeout=1)
    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.success is False


@pytest.mark.asyncio
async def test_find_and_get_or_create():
    """find_sandbox matches labels on metadata; get_or_create reuses a match."""
    running = FakeSandbox(_info("sbx-a", state="RUNNING", metadata={"task": "x"}))
    stopped = FakeSandbox(_info("sbx-b", state="PAUSED", metadata={"task": "y"}))
    created = FakeSandbox(_info("sbx-new", metadata={"task": "z"}))
    client = FakeClient(
        create_result=created,
        list_result=[running, stopped],
        sandboxes={"sbx-a": running, "sbx-b": stopped},
    )
    provider = _provider(client)

    found = await provider.find_sandbox({"task": "x"})
    assert found is not None and found.id == "sbx-a"

    # Paused sandbox is not a running reuse candidate.
    assert await provider.find_sandbox({"task": "y"}) is None

    reused = await provider.get_or_create_sandbox(SandboxConfig(labels={"task": "x"}))
    assert reused.id == "sbx-a"
    assert client.create_calls == []

    made = await provider.get_or_create_sandbox(SandboxConfig(labels={"task": "z"}))
    assert made.id == "sbx-new"
    assert len(client.create_calls) == 1


@pytest.mark.asyncio
async def test_file_upload_and_download(tmp_path):
    """upload_file / download_file delegate to the SDK fs API."""
    sb = FakeSandbox(_info("sbx-1"))
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))

    local = tmp_path / "in.txt"
    local.write_text("hello tenki")
    assert await provider.upload_file("sbx-1", str(local), "/work/in.txt") is True
    assert sb.fs.uploads == [(str(local), "/work/in.txt")]

    dest = tmp_path / "out.txt"
    assert await provider.download_file("sbx-1", "/work/out.txt", str(dest)) is True
    assert sb.fs.downloads == [("/work/out.txt", str(dest))]
    assert dest.read_bytes() == b"downloaded"


@pytest.mark.asyncio
async def test_stream_execution():
    """stream_execution yields stdout chunks then a trailing stderr block."""
    proc = FakeProcess([b"chunk-1 ", b"chunk-2"], stderr=b"warn")
    sb = FakeSandbox(_info("sbx-1"), process=proc)
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))

    chunks = [c async for c in provider.stream_execution("sbx-1", "echo test")]
    joined = "".join(chunks)
    assert "chunk-1 chunk-2" in joined
    assert "[stderr]: warn" in joined


@pytest.mark.asyncio
async def test_stream_execution_kills_process_when_abandoned():
    """Abandoning the generator mid-stream kills the remote process (no leak)."""
    proc = FakeProcess([b"chunk-1 ", b"chunk-2", b"chunk-3"])
    sb = FakeSandbox(_info("sbx-1"), process=proc)
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))

    agen = provider.stream_execution("sbx-1", "echo test")
    assert await agen.__anext__() == "chunk-1 "
    await agen.aclose()  # abandon before consuming the rest
    assert proc.killed is True


@pytest.mark.asyncio
async def test_stream_execution_maps_wait_error():
    """Errors from proc.wait() are normalized (timeout -> SandboxTimeoutError)."""
    proc = FakeProcess([b"partial"], wait_error=CommandTimeoutError("deadline"))
    sb = FakeSandbox(_info("sbx-1"), process=proc)
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))

    with pytest.raises(SandboxTimeoutError):
        async for _ in provider.stream_execution("sbx-1", "sleep 999"):
            pass
    assert proc.killed is True


@pytest.mark.asyncio
async def test_stream_execution_wait_timeout():
    """A hung proc.wait() is bounded by timeout and raises SandboxTimeoutError."""
    proc = FakeProcess([b"partial"], wait_hang=True)
    sb = FakeSandbox(_info("sbx-1"), process=proc)
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))

    with pytest.raises(SandboxTimeoutError):
        async for _ in provider.stream_execution("sbx-1", "sleep 999", timeout=0.01):
            pass
    assert proc.killed is True


@pytest.mark.asyncio
async def test_resolve_rejects_sandbox_being_destroyed():
    """_resolve refuses to return a handle for an id under destruction."""
    sb = FakeSandbox(_info("sbx-1"))
    provider = _provider(FakeClient(sandboxes={"sbx-1": sb}))
    provider._destroying.add("sbx-1")

    with pytest.raises(SandboxNotFoundError):
        await provider.execute_command("sbx-1", "echo hi")


@pytest.mark.asyncio
async def test_health_check():
    """health_check returns True when who_am_i succeeds, else False."""
    assert await _provider(FakeClient()).health_check() is True
    assert await _provider(FakeClient(whoami_error=RuntimeError("down"))).health_check() is False


@pytest.mark.asyncio
async def test_aclose_closes_client():
    client = FakeClient()
    provider = _provider(client)
    await provider.health_check()
    await provider.aclose()
    assert client.closed is True


# --------------------------------------------------------------------------
# Live integration (skipped without a key)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.tenki
async def test_tenki_live_integration():
    """Live smoke test against a real Tenki account (skipped without a key)."""
    api_key = os.getenv("TENKI_API_KEY")
    if not api_key:
        pytest.skip("TENKI_API_KEY not configured")

    provider = TenkiProvider(api_key=api_key)
    sandbox = None
    try:
        sandbox = await provider.create_sandbox(SandboxConfig(labels={"session": "pytest-live"}))
        result = await provider.execute_command(sandbox.id, "echo tenki")
        assert result.success
        assert "tenki" in result.stdout
        assert await provider.health_check() is True
    finally:
        if sandbox is not None:
            await provider.destroy_sandbox(sandbox.id)
        await provider.aclose()
