import pytest

from sandboxes.base import SandboxConfig
from sandboxes.manager import SandboxManager
from tests.test_manager import MockProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("default_fails", [False, True])
async def test_fallback_list_keeps_default_provider_first(default_fails):
    manager = SandboxManager(default_provider="primary")
    primary = MockProvider("primary", should_fail=default_fails)
    fallback = MockProvider("fallback")
    manager.providers = {"primary": primary, "fallback": fallback}
    sandbox = await manager.create_sandbox(SandboxConfig(), fallback_providers=["fallback"])
    assert primary.create_called
    assert sandbox.provider == ("fallback" if default_fails else "primary")
    assert fallback.create_called is default_fails
