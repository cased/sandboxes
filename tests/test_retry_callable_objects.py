from functools import partial

import pytest

from sandboxes.exceptions import ProviderError
from sandboxes.retry import RetryConfig, RetryHandler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wrap", [lambda fn: partial(fn, "result"), lambda fn: CallableOperation(fn)]
)
async def test_retry_accepts_callable_without_a_name(wrap):
    calls = 0

    async def operation(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderError("temporary failure")
        return value

    handler = RetryHandler(RetryConfig(max_retries=1, initial_delay=0, jitter=False))
    assert await handler.execute(wrap(operation)) == "result"
    assert calls == 2


class CallableOperation:
    def __init__(self, operation):
        self.operation = operation

    async def __call__(self):
        return await self.operation("result")


@pytest.mark.asyncio
async def test_retry_preserves_original_error_for_unnamed_callable():
    async def operation():
        raise ValueError("original failure")

    handler = RetryHandler(RetryConfig(max_retries=0))
    with pytest.raises(ValueError, match="original failure"):
        await handler.execute(partial(operation))
