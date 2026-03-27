"""Regression tests for E2B provider behaviors."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import sandboxes.providers.e2b as e2b_module
from sandboxes.exceptions import ProviderError, SandboxNotFoundError
from sandboxes.providers.e2b import E2BProvider


class _ListedSandbox:
    def __init__(self, sandbox_id: str, metadata: dict[str, str]):
        self.sandbox_id = sandbox_id
        self.metadata = metadata
        self.started_at = datetime.now()
        self.state = "running"
        self.template_id = "base"
        self.name = sandbox_id
        self.end_at = None


class _Paginator:
    def __init__(self, items):
        self._items = items

    async def next_items(self):
        return self._items


@pytest.mark.asyncio
async def test_find_sandbox_handles_api_listed_untracked_sandbox(monkeypatch):
    class _FakeE2B:
        @staticmethod
        def list(api_key=None):  # noqa: ARG004
            return _Paginator([_ListedSandbox("sb-untracked", {"env": "prod"})])

    monkeypatch.setattr(e2b_module, "E2BSandbox", _FakeE2B)

    provider = E2BProvider(api_key="test-key")
    sandbox = await provider.find_sandbox({"env": "prod"})

    assert sandbox is not None
    assert sandbox.id == "sb-untracked"


@pytest.mark.asyncio
async def test_list_sandboxes_supports_legacy_list_signature(monkeypatch):
    class _FakeE2BLegacy:
        @staticmethod
        def list():
            return _Paginator([_ListedSandbox("sb-legacy", {"team": "infra"})])

    monkeypatch.setattr(e2b_module, "E2BSandbox", _FakeE2BLegacy)

    provider = E2BProvider(api_key="test-key")
    sandboxes = await provider.list_sandboxes(labels={"team": "infra"})

    assert len(sandboxes) == 1
    assert sandboxes[0].id == "sb-legacy"


@pytest.mark.asyncio
async def test_create_retries_when_e2b_transport_bound_to_closed_loop(monkeypatch):
    calls = {"count": 0}

    class _FakeSandbox:
        sandbox_id = "sb-retry"

    class _FakeE2B:
        @staticmethod
        async def create(template=None, envs=None, api_key=None, timeout=None):  # noqa: ARG004
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("Event loop is closed")
            return _FakeSandbox()

    monkeypatch.setattr(e2b_module, "E2BSandbox", _FakeE2B)

    provider = E2BProvider(api_key="test-key")
    sandbox = await provider._create_e2b_sandbox(template_id="base", env_vars={})

    assert sandbox.sandbox_id == "sb-retry"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_run_code_returns_rich_outputs(monkeypatch):
    """run_code maps Execution results to the hopx-compatible dict shape."""
    from e2b_code_interpreter.models import Execution, Logs, Result

    sandbox_id = "sb-code-123"
    provider = E2BProvider(api_key="test-key")
    provider._sandboxes[sandbox_id] = {"last_accessed": 0.0}

    # Build a mock Execution with one PNG result and stdout
    png_result = Result(png="iVBORw0KGgo=", text="<Figure>", is_main_result=True)
    execution = Execution(
        results=[png_result],
        logs=Logs(stdout=["Plot created"], stderr=[]),
        error=None,
    )

    mock_interpreter = MagicMock()
    mock_interpreter.run_code = AsyncMock(return_value=execution)

    mock_connect = AsyncMock(return_value=mock_interpreter)
    monkeypatch.setattr(e2b_module, "E2BCodeInterpreter", MagicMock(connect=mock_connect))
    monkeypatch.setattr(e2b_module, "E2B_CODE_INTERPRETER_AVAILABLE", True)

    result = await provider.run_code(sandbox_id, code="import matplotlib")

    assert result["success"] is True
    assert result["stdout"] == "Plot created"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["execution_time"] >= 0.0

    types = [o["type"] for o in result["rich_outputs"]]
    assert "png" in types
    assert "text" in types
    png_entry = next(o for o in result["rich_outputs"] if o["type"] == "png")
    assert png_entry["data"] == "iVBORw0KGgo="

    mock_connect.assert_awaited_once_with(sandbox_id, api_key="test-key")


@pytest.mark.asyncio
async def test_run_code_raises_when_not_available(monkeypatch):
    """run_code raises ProviderError when e2b_code_interpreter is not installed."""
    monkeypatch.setattr(e2b_module, "E2B_CODE_INTERPRETER_AVAILABLE", False)

    provider = E2BProvider(api_key="test-key")
    provider._sandboxes["sb-x"] = {"last_accessed": 0.0}

    with pytest.raises(ProviderError, match="e2b_code_interpreter not installed"):
        await provider.run_code("sb-x", code="print(1)")


@pytest.mark.asyncio
async def test_run_code_raises_for_unknown_sandbox(monkeypatch):
    """run_code raises SandboxNotFoundError for an untracked sandbox_id."""
    monkeypatch.setattr(e2b_module, "E2B_CODE_INTERPRETER_AVAILABLE", True)

    provider = E2BProvider(api_key="test-key")

    with pytest.raises(SandboxNotFoundError):
        await provider.run_code("sb-does-not-exist", code="print(1)")
