"""Regression tests for E2B provider behaviors."""

from datetime import datetime

import pytest

import sandboxes.providers.e2b as e2b_module
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
