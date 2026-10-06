import pytest

from sandboxes.exceptions import ProviderError
from sandboxes.retry import RetryConfig, RetryHandler


@pytest.mark.asyncio
async def test_configured_predicate_can_veto_retry():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        raise ProviderError("do not repeat this operation")

    handler = RetryHandler(
        RetryConfig(
            max_retries=2, initial_delay=0, jitter=False, should_retry=lambda error, attempt: False
        )
    )
    with pytest.raises(ProviderError, match="do not repeat this operation"):
        await handler.execute(operation)
    assert calls == 1
