import subprocess

import pytest
from click.testing import CliRunner

from sandboxes.base import ExecutionResult, Sandbox, SandboxState
from sandboxes.cli import cli


class LocalProvider:
    async def create_sandbox(self, config):
        return Sandbox(id="local", provider="local", state=SandboxState.RUNNING)

    async def execute_command(self, sandbox_id, command, **kwargs):
        result = subprocess.run(
            ["/bin/sh", "-c", command], capture_output=True, text=True, timeout=5
        )
        return ExecutionResult(result.returncode, result.stdout, result.stderr)

    async def destroy_sandbox(self, sandbox_id):
        return True


@pytest.mark.parametrize("from_file", [False, True])
def test_python_source_quotes_survive_cli_shell_wrapper(monkeypatch, tmp_path, from_file):
    monkeypatch.setattr("sandboxes.cli.get_provider", lambda name: LocalProvider())
    source = "print('hello')\nprint(\"it's quoted\")\n"
    args = ["run", "--language", "python"]
    if from_file:
        file = tmp_path / "source.py"
        file.write_text(source)
        args += ["--file", str(file)]
    result = CliRunner().invoke(cli, args, input=None if from_file else source)
    assert result.exit_code == 0, result.output
    assert result.stdout == "hello\nit's quoted\n"
