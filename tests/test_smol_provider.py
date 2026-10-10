"""Smol provider lifecycle and routing without depending on KVM or Cloud credentials."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("smol")

from sandboxes import Sandbox as HighLevelSandbox
from sandboxes.base import SandboxConfig, SandboxState
from sandboxes.exceptions import ProviderError, SandboxError, SandboxNotFoundError
from sandboxes.providers.smol import SmolProvider


@pytest.fixture
def sdk():
    machine = MagicMock()
    machine.id = "smol-123"
    machine.exec.return_value = SimpleNamespace(
        exit_code=0,
        stdout="hello\n",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )
    machine.read_file.return_value = b"guest-data"
    summary = SimpleNamespace(
        id="smol-123",
        state="running",
        image="python:3.12-alpine",
        labels={"cased-sandboxes": "true", "project": "test"},
        created_at="2026-01-01T00:00:00Z",
    )
    with (
        patch("sandboxes.providers.smol.Machine.create", return_value=machine) as create,
        patch("sandboxes.providers.smol.Machine.connect", return_value=machine) as connect,
        patch("sandboxes.providers.smol.Machine.list", return_value=[summary]) as list_machines,
    ):
        yield SimpleNamespace(
            machine=machine, create=create, connect=connect, list=list_machines, summary=summary
        )


@pytest.mark.asyncio
async def test_local_vm_creation_execution_and_deletion(sdk):
    provider = SmolProvider()
    sandbox = await provider.create_sandbox(
        SandboxConfig(
            image="python:3.12-alpine",
            labels={"project": "test"},
            env_vars={"TEST_ONLY": "set"},
            cpu_cores=1.5,
            provider_config={"network": False},
        )
    )
    config, conn = sdk.create.call_args.args
    assert conn.target == "local"
    assert config.persistent is True and config.ttl_seconds is None
    assert config.resources.cpus == 2 and config.resources.network is False
    assert config.env == {"TEST_ONLY": "set"}
    assert config.labels == {"project": "test", "cased-sandboxes": "true"}
    assert sandbox.id == "smol-123" and sandbox.labels == {"project": "test"}
    assert sandbox.state == SandboxState.RUNNING
    assert sandbox.created_at.year == 2026

    result = await provider.execute_command(
        "smol-123", "printf hello", timeout=7, env_vars={"X": "1"}
    )
    assert result.stdout == "hello\n" and result.success
    command, options = sdk.machine.exec.call_args.args
    assert command == ["sh", "-lc", "printf hello"]
    assert options.timeout == 7 and options.env == {"X": "1"}
    assert await provider.destroy_sandbox("smol-123")
    sdk.machine.delete.assert_called_once_with()


@pytest.mark.asyncio
async def test_cloud_is_explicit_and_ttl_is_bounded(sdk):
    sdk.summary.state = "started"
    provider = SmolProvider(target="cloud", ttl_seconds=120)
    sandbox = await provider.create_sandbox(SandboxConfig())
    assert sandbox.state == SandboxState.RUNNING
    config, conn = sdk.create.call_args.args
    assert conn.target == "cloud" and config.ttl_seconds == 120
    with pytest.raises(ProviderError, match="TTL must be positive"):
        SmolProvider(target="cloud", ttl_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("state,action", [("stopped", "start"), ("paused", "resume")])
async def test_cloud_reuses_stopped_or_paused_sandbox(sdk, state, action):
    sdk.summary.state = state
    sdk.machine.state.return_value = state
    provider = SmolProvider(target="cloud")
    result = await provider.execute_command("smol-123", "printf hello")
    assert result.success
    getattr(sdk.machine, action).assert_called_once_with()
    (sdk.machine.resume if action == "start" else sdk.machine.start).assert_not_called()
    await provider.upload_file("smol-123", __file__, "/workspace/test.py")
    assert getattr(sdk.machine, action).call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["local", "cloud"])
async def test_get_or_create_reuses_a_stopped_labeled_sandbox(sdk, target):
    sdk.summary.state = "stopped"
    sdk.machine.state.return_value = "stopped"
    if target == "cloud":
        sdk.machine.start.side_effect = lambda: setattr(sdk.summary, "state", "started")
    else:
        sdk.connect.side_effect = lambda *args: (
            setattr(sdk.summary, "state", "running") or sdk.machine
        )
    provider = SmolProvider(target=target)
    sandbox = await provider.get_or_create_sandbox(SandboxConfig(labels={"project": "test"}))
    assert sandbox.id == "smol-123"
    assert sandbox.state == SandboxState.RUNNING
    sdk.create.assert_not_called()
    if target == "cloud":
        sdk.machine.start.assert_called_once_with()
    else:
        sdk.machine.start.assert_not_called()


@pytest.mark.asyncio
async def test_failed_setup_deletes_created_machine(sdk):
    sdk.machine.exec.return_value = SimpleNamespace(
        exit_code=1, stdout="", stderr="failed", stdout_truncated=False, stderr_truncated=False
    )
    with pytest.raises(SandboxError, match="setup command failed"):
        await SmolProvider().create_sandbox(SandboxConfig(setup_commands=["false"]))
    sdk.machine.delete.assert_called_once_with()


@pytest.mark.asyncio
async def test_failed_listing_deletes_created_machine(sdk):
    sdk.list.side_effect = RuntimeError("local list failed")
    with pytest.raises(RuntimeError, match="local list failed"):
        await SmolProvider().create_sandbox(SandboxConfig())
    sdk.machine.delete.assert_called_once_with()


@pytest.mark.asyncio
async def test_only_managed_sandboxes_are_discoverable_or_mutable(sdk):
    provider = SmolProvider()
    sdk.summary.labels = {"other": "project"}
    assert await provider.get_sandbox("smol-123") is None
    with pytest.raises(SandboxNotFoundError):
        await provider.execute_command("smol-123", "true")
    assert not await provider.destroy_sandbox("smol-123")
    assert not sdk.connect.called
    sdk.list.assert_called_with(provider._connection, {"cased-sandboxes": "true"})


@pytest.mark.asyncio
async def test_file_transfer_uses_guest_api_and_validates_local_paths(sdk, tmp_path):
    provider = SmolProvider()
    source = tmp_path / "source.txt"
    target = tmp_path / "result.txt"
    source.write_bytes(b"host-data")
    assert await provider.upload_file("smol-123", str(source), "/workspace/source.txt")
    sdk.machine.write_file.assert_called_once_with("/workspace/source.txt", b"host-data")
    assert await provider.download_file("smol-123", "/workspace/result.txt", str(target))
    assert target.read_bytes() == b"guest-data"
    with pytest.raises(SandboxError, match="traversal"):
        await provider.upload_file("smol-123", str(tmp_path / ".." / "source.txt"), "/workspace/x")


@pytest.mark.asyncio
async def test_cloud_transfer_rejects_unsafe_paths_before_connecting(sdk, tmp_path):
    provider = SmolProvider(target="cloud")
    source = tmp_path / "source.txt"
    source.write_bytes(b"host data")
    destination = tmp_path / "destination.txt"
    for suffix in ("#draft", "?v=2", "%20draft", "\nname", "\x00name"):
        path = f"/workspace/report{suffix}.txt"
        with pytest.raises(SandboxError, match="Smol Cloud file path"):
            await provider.upload_file("smol-123", str(source), path)
        with pytest.raises(SandboxError, match="Smol Cloud file path"):
            await provider.download_file("smol-123", path, str(destination))
    sdk.list.assert_not_called()
    sdk.connect.assert_not_called()
    sdk.machine.write_file.assert_not_called()
    sdk.machine.read_file.assert_not_called()
    assert not destination.exists()


@pytest.mark.asyncio
async def test_local_transfer_keeps_url_delimiters(sdk, tmp_path):
    provider = SmolProvider()
    source = tmp_path / "source.txt"
    source.write_bytes(b"local filename")
    destination = tmp_path / "destination.txt"
    path = "/workspace/report#draft%20.txt"
    assert await provider.upload_file("smol-123", str(source), path)
    assert await provider.download_file("smol-123", path, str(destination))
    sdk.machine.write_file.assert_called_once_with(path, b"local filename")
    sdk.machine.read_file.assert_called_once_with(path)


@pytest.mark.asyncio
async def test_rejects_contradictory_network_policy_before_provisioning(sdk):
    provider = SmolProvider()
    with pytest.raises(ProviderError, match="allow lists require network"):
        await provider.create_sandbox(
            SandboxConfig(provider_config={"network": False, "allow_hosts": ["example.com"]})
        )
    sdk.create.assert_not_called()


def test_high_level_explicit_cloud_routing(sdk, monkeypatch):
    monkeypatch.delenv("SMOL_SANDBOX_TARGET", raising=False)
    monkeypatch.setattr(HighLevelSandbox, "_manager", None)
    monkeypatch.setattr(HighLevelSandbox, "_auto_configured", False)
    HighLevelSandbox.configure(smol_target="cloud", default_provider="smol")
    manager = HighLevelSandbox._ensure_manager()
    assert manager.default_provider == "smol"
    assert manager.get_provider("smol").target == "cloud"
