import pytest

from sandboxes.base import Sandbox, SandboxConfig, SandboxState
from sandboxes.pool import PoolConfig, SandboxPool


class Provider:
    name = "fake"

    def __init__(self, account):
        self.account = account
        self.created = 0

    async def create_sandbox(self, config):
        self.created += 1
        return Sandbox(
            id=f"{self.account}-{self.created}",
            provider=self.name,
            state=SandboxState.RUNNING,
            labels=config.labels,
        )

    async def destroy_sandbox(self, sandbox_id):
        return True


@pytest.mark.asyncio
async def test_matching_labels_do_not_reuse_another_provider_account():
    pool = SandboxPool(PoolConfig(auto_cleanup=False))
    first = Provider("first-account")
    second = Provider("second-account")
    config = SandboxConfig(labels={"project": "demo"})
    try:
        first_sandbox = await pool.acquire(first, config)
        await pool.release(first_sandbox.id)
        second_sandbox = await pool.acquire(second, config)
        assert second_sandbox.id == "second-account-1"
        assert second.created == 1
        await pool.release(second_sandbox.id)
        reused = await pool.acquire(first, config)
        assert reused.id == first_sandbox.id
        assert first.created == 1
    finally:
        await pool.stop()
