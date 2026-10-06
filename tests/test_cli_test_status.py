import pytest
from click.testing import CliRunner

from sandboxes.base import ExecutionResult, Sandbox, SandboxState
from sandboxes.cli import cli


class ProbeProvider:
    def __init__(self, exit_code, stdout, destroyed=True):
        self.result = ExecutionResult(exit_code, stdout, "")
        self.destroyed = destroyed

    async def create_sandbox(self, config):
        return Sandbox(id="probe", provider="probe", state=SandboxState.RUNNING)

    async def execute_command(self, *args):
        return self.result

    async def destroy_sandbox(self, sandbox_id):
        return self.destroyed


@pytest.mark.parametrize("exit_code,stdout", [(1, ""), (0, "wrong output")])
def test_failed_provider_probe_is_reported_as_failure(monkeypatch, exit_code, stdout):
    monkeypatch.setattr("sandboxes.cli.get_provider", lambda name: ProbeProvider(exit_code, stdout))
    result = CliRunner().invoke(cli, ["test", "--provider", "e2b"])
    assert result.exit_code == 1
    assert "Working" not in result.stdout


def test_successful_provider_probe_returns_zero(monkeypatch):
    monkeypatch.setattr(
        "sandboxes.cli.get_provider", lambda name: ProbeProvider(0, "Hello from CLI test")
    )
    result = CliRunner().invoke(cli, ["test", "--provider", "e2b"])
    assert result.exit_code == 0
    assert "Working" in result.stdout


def test_failed_probe_cleanup_returns_nonzero(monkeypatch):
    monkeypatch.setattr(
        "sandboxes.cli.get_provider",
        lambda name: ProbeProvider(0, "Hello from CLI test", destroyed=False),
    )
    result = CliRunner().invoke(cli, ["test", "--provider", "e2b"])
    assert result.exit_code == 1
    assert "Destroyed" not in result.stdout
    assert "Working" not in result.stdout
