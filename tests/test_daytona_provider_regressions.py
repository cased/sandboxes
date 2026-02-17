"""Regression tests for Daytona provider behaviors."""

import pytest

import sandboxes.providers.daytona as daytona_module
from sandboxes.base import SandboxConfig
from sandboxes.providers.daytona import DaytonaProvider


@pytest.mark.asyncio
async def test_image_create_includes_labels_env_and_rounded_memory(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeClient:
        def create(self, params, timeout=None):
            captured["params"] = params
            captured["timeout"] = timeout
            return type(
                "CreatedSandbox",
                (),
                {
                    "id": "sb-daytona-1",
                    "state": "running",
                    "labels": getattr(params, "labels", {}),
                    "created_at": None,
                    "snapshot": None,
                },
            )()

    monkeypatch.setattr(daytona_module, "Daytona", lambda: _FakeClient())

    provider = DaytonaProvider(api_key="test-key")
    sandbox = await provider.create_sandbox(
        SandboxConfig(
            image="python:3.12",
            cpu_cores=1,
            memory_mb=512,
            labels={"team": "platform"},
            env_vars={"HELLO": "world"},
            timeout_seconds=123,
        )
    )

    params = captured["params"]
    assert sandbox.id == "sb-daytona-1"
    assert params.labels == {"team": "platform"}
    assert params.env_vars == {"HELLO": "world"}
    assert params.resources.memory == 1
    assert captured["timeout"] == 123
