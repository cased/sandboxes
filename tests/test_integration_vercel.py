"""Real integration tests for Vercel provider."""

import os

import pytest

from sandboxes import SandboxConfig
from sandboxes.providers.vercel import VercelProvider


@pytest.mark.integration
@pytest.mark.vercel
@pytest.mark.asyncio
async def test_create_execute_destroy_vercel():
    """Create, execute, list, and destroy a real Vercel sandbox."""
    token = (
        os.getenv("VERCEL_TOKEN")
        or os.getenv("VERCEL_API_TOKEN")
        or os.getenv("VERCEL_ACCESS_TOKEN")
    )
    project_id = os.getenv("VERCEL_PROJECT_ID")
    team_id = os.getenv("VERCEL_TEAM_ID")

    if not (token and project_id and team_id):
        pytest.skip("Vercel credentials not configured")

    provider = VercelProvider(token=token, project_id=project_id, team_id=team_id)
    sandbox = await provider.create_sandbox(
        SandboxConfig(
            image="node22",
            labels={"test": "integration", "provider": "vercel"},
        )
    )

    try:
        result = await provider.execute_command(sandbox.id, "echo 'hello from vercel'")
        assert result.success
        assert "hello from vercel" in result.stdout

        sandboxes = await provider.list_sandboxes()
        assert any(sb.id == sandbox.id for sb in sandboxes)
    finally:
        await provider.destroy_sandbox(sandbox.id)
