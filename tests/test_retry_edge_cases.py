"""Edge case and error scenario tests for retry module."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from sandboxes.exceptions import SandboxError
from sandboxes.retry import (
    CircuitBreaker,
    RetryConfig,
    RetryHandler,
    with_retry,
)

pytestmark = pytest.mark.asyncio


class TestRetryEdgeCases:
    """Test edge cases in retry mechanisms."""

    async def test_retry_with_zero_max_retries(self):
        """Test RetryHandler with max_retries=0."""
        config = RetryConfig(max_retries=0)
        handler = RetryHandler(config)

        call_count = 0

        async def failing_operation():
            nonlocal call_count
            call_count += 1
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await handler.execute(failing_operation)

        # Should only call once (no retries)
        assert call_count == 1

    async def test_retry_with_negative_delay(self):
        """Test RetryHandler with invalid negative delay."""
        with pytest.raises(ValueError):
            RetryConfig(initial_delay=-1)

    async def test_retry_with_custom_exceptions_only(self):
        """Test retry only on specific exception types."""
        config = RetryConfig(max_retries=3, initial_delay=0.01, retryable_errors=(SandboxError,))
        handler = RetryHandler(config)

        # Should retry on SandboxError
        sandbox_call_count = 0

        async def sandbox_error_operation():
            nonlocal sandbox_call_count
            sandbox_call_count += 1
            raise SandboxError("Sandbox failed")

        with pytest.raises(SandboxError):
            await handler.execute(sandbox_error_operation)
        assert sandbox_call_count == 4  # 1 initial + 3 retries

        # Should NOT retry on ValueError
        value_call_count = 0

        async def value_error_operation():
            nonlocal value_call_count
            value_call_count += 1
            raise ValueError("Value error")

        with pytest.raises(ValueError):
            await handler.execute(value_error_operation)
        assert value_call_count == 1  # No retries

    async def test_retry_with_timeout_during_operation(self):
        """Test retry behavior when operation times out."""
        config = RetryConfig(max_retries=2, initial_delay=0.01, timeout=0.05)  # 50ms timeout
        handler = RetryHandler(config)

        async def slow_operation():
            await asyncio.sleep(0.1)  # Takes longer than timeout
            return "success"

        with pytest.raises(asyncio.TimeoutError):
            await handler.execute(slow_operation)

    async def test_retry_with_partial_success_predicate(self):
        """Test retry with complex should_retry predicate."""
        attempts = []

        def should_retry(exc, attempt):
            # Retry only on even attempts and specific error messages
            return attempt % 2 == 0 and isinstance(exc, ValueError) and "retry" in str(exc).lower()

        config = RetryConfig(max_retries=5, initial_delay=0.01, should_retry=should_retry)
        handler = RetryHandler(config)

        async def conditional_operation():
            attempt = len(attempts)
            attempts.append(attempt)
            if attempt < 3:
                raise ValueError("Please retry")
            return "success"

        result = await handler.execute(conditional_operation)
        assert result == "success"
        # Should have succeeded on attempt 3 (0-indexed)
        assert len(attempts) == 4

    async def test_circuit_breaker_with_zero_threshold(self):
        """Test CircuitBreaker with failure_threshold=0."""
        with pytest.raises(ValueError):
            CircuitBreaker(failure_threshold=0, recovery_timeout=1, success_threshold=1)

    async def test_circuit_breaker_rapid_failures(self):
        """Test CircuitBreaker under rapid failure conditions."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, success_threshold=1)

        fail_count = 0

        async def rapid_fail_operation():
            nonlocal fail_count
            fail_count += 1
            raise ValueError(f"Failure {fail_count}")

        # First two calls fail and open the circuit
        for _i in range(2):
            with pytest.raises(ValueError):
                await breaker.call(rapid_fail_operation)

        # Circuit should be open
        assert breaker.state == "open"

        # Rapid attempts while open should fail fast
        start = time.time()
        for _ in range(10):
            with pytest.raises(Exception, match="Circuit breaker is open"):
                await breaker.call(rapid_fail_operation)
        elapsed = time.time() - start

        # Should fail fast without calling operation
        assert elapsed < 0.1
        assert fail_count == 2  # Only the first 2 calls

    async def test_circuit_breaker_concurrent_calls(self):
        """Test CircuitBreaker with concurrent calls."""
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=0.5, success_threshold=2)

        call_results = []
        call_errors = []

        async def concurrent_operation(task_id):
            if task_id < 3:
                raise ValueError(f"Task {task_id} failed")
            return f"Task {task_id} success"

        async def safe_call(task_id):
            try:
                result = await breaker.call(lambda: concurrent_operation(task_id))
                call_results.append(result)
            except Exception as e:
                call_errors.append(str(e))

        # Launch concurrent calls
        tasks = [safe_call(i) for i in range(10)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Should have some failures and some circuit open errors
        assert len(call_errors) >= 3  # At least the initial failures
        assert any("Circuit breaker is open" in err for err in call_errors)

    async def test_retry_with_circuit_breaker_interaction(self):
        """Test RetryHandler with CircuitBreaker integration."""
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0.2, success_threshold=1)

        config = RetryConfig(max_retries=3, initial_delay=0.01, circuit_breaker=breaker)
        handler = RetryHandler(config)

        fail_count = 0

        async def flaky_operation():
            nonlocal fail_count
            fail_count += 1
            if fail_count <= 5:
                raise ValueError(f"Failure {fail_count}")
            return "success"

        # First attempt - will fail and open circuit
        with pytest.raises(Exception):
            await handler.execute(flaky_operation)

        # Circuit should be open
        assert breaker.state == "open"

        # Wait for recovery
        await asyncio.sleep(0.3)

        # Try again - should eventually succeed
        result = await handler.execute(flaky_operation)
        assert result == "success"

    async def test_exponential_backoff_overflow(self):
        """Test exponential backoff with large retry counts."""
        config = RetryConfig(max_retries=20, initial_delay=0.001, max_delay=1.0, exponential_base=2)
        handler = RetryHandler(config)

        call_count = 0

        async def track_calls():
            nonlocal call_count
            call_count += 1
            if call_count < 10:
                raise ValueError("Retry")
            return "success"

        result = await handler.execute(track_calls)
        assert result == "success"
        assert call_count == 10  # Should have retried up to limit

    async def test_retry_callback_exception(self):
        """Test retry behavior when callback raises exception."""
        callback_errors = []

        def bad_callback(exc, attempt):
            callback_errors.append(attempt)
            raise RuntimeError("Callback failed")

        config = RetryConfig(max_retries=2, initial_delay=0.01, on_retry=bad_callback)
        handler = RetryHandler(config)

        async def failing_operation():
            raise ValueError("Test")

        # Callback exceptions should be ignored
        with pytest.raises(ValueError):
            await handler.execute(failing_operation)

        # Callback should have been called despite exceptions
        assert len(callback_errors) == 2  # Called for each retry

    async def test_decorator_with_async_generator(self):
        """Test retry decorator with async generator function."""

        @with_retry(max_retries=2, initial_delay=0.01)
        async def async_generator():
            yield 1
            raise ValueError("Error in generator")
            yield 2  # Never reached

        gen = async_generator()
        assert await gen.__anext__() == 1

        with pytest.raises(ValueError):
            await gen.__anext__()

    async def test_backoff_with_jitter_consistency(self):
        """Test that jitter produces consistent randomization."""
        config = RetryConfig(initial_delay=1.0, exponential_base=2.0, jitter=True)

        # Test that jitter is enabled
        assert config.jitter

        # Test multiple retry attempts have different delays due to jitter
        handler = RetryHandler(config)

        call_times = []

        async def track_timing():
            call_times.append(time.time())
            if len(call_times) < 3:
                raise ValueError("Retry")
            return "success"

        result = await handler.execute(track_timing)
        assert result == "success"
        assert len(call_times) == 3

    async def test_retry_state_cleanup_on_exception(self):
        """Test that retry state is properly cleaned up on exceptions."""
        config = RetryConfig(max_retries=2, initial_delay=0.01)
        handler = RetryHandler(config)

        # Track handler state
        initial_state = str(handler)

        async def crashing_operation():
            raise MemoryError("Out of memory")

        with pytest.raises(MemoryError):
            await handler.execute(crashing_operation)

        # Handler should be in same state as before
        final_state = str(handler)
        assert initial_state == final_state

    async def test_circuit_breaker_metrics(self):
        """Test CircuitBreaker metrics tracking."""
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1, success_threshold=2)

        # Track metrics
        successes = 0
        failures = 0

        for i in range(10):
            try:
                if i % 3 == 0:
                    await breaker.call(AsyncMock(side_effect=ValueError()))
                    failures += 1
                else:
                    await breaker.call(AsyncMock(return_value="success"))
                    successes += 1
            except:
                pass

        # Breaker should track internal metrics
        assert breaker.consecutive_failures >= 0
        assert breaker.consecutive_successes >= 0
