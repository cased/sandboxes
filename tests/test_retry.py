"""Tests for retry logic and circuit breaker functionality."""

import asyncio
import time

import pytest

from sandboxes.exceptions import ProviderError, SandboxError
from sandboxes.retry import (
    CircuitBreaker,
    CircuitBreakerState,
    ExponentialBackoff,
    LinearBackoff,
    RetryConfig,
    RetryHandler,
    with_retry,
)


class TestRetryHandler:
    """Test retry handler functionality."""

    @pytest.fixture
    def retry_config(self):
        """Create test retry configuration."""
        return RetryConfig(
            max_retries=3,
            initial_delay=0.1,
            max_delay=1.0,
            exponential_base=2.0,
            jitter=False,  # Disable jitter for predictable tests
            allow_additional_attempt=False,
        )

    @pytest.fixture
    def retry_handler(self, retry_config):
        """Create retry handler."""
        return RetryHandler(retry_config)

    @pytest.mark.asyncio
    async def test_successful_operation(self, retry_handler):
        """Test operation that succeeds immediately."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await retry_handler.execute(operation)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, retry_handler):
        """Test retry on transient failure."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise SandboxError("Temporary failure")
            return "success"

        result = await retry_handler.execute(operation)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, retry_handler):
        """Test max retries exceeded."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            raise SandboxError(f"Failure {call_count}")

        with pytest.raises(SandboxError) as exc_info:
            await retry_handler.execute(operation)

        assert "Failure" in str(exc_info.value)
        assert call_count == 4  # Initial + 3 retries

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, retry_handler):
        """Test exponential backoff timing."""
        retry_handler.config.initial_delay = 0.01
        retry_handler.config.exponential_base = 2.0

        call_times = []

        async def operation():
            call_times.append(time.time())
            if len(call_times) < 3:
                raise SandboxError("Retry me")
            return "success"

        time.time()
        result = await retry_handler.execute(operation)
        assert result == "success"

        # Check delays are approximately exponential
        if len(call_times) >= 3:
            delay1 = call_times[1] - call_times[0]
            delay2 = call_times[2] - call_times[1]
            # Second delay should be roughly 2x the first
            assert delay2 > delay1 * 1.5

    @pytest.mark.asyncio
    async def test_retry_with_predicate(self, retry_handler):
        """Test retry with custom predicate."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Don't retry this")
            return "success"

        # Should not retry on ValueError
        def should_retry(error):
            return not isinstance(error, ValueError)

        retry_handler.should_retry = should_retry

        with pytest.raises(ValueError):
            await retry_handler.execute(operation)

        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_retry_with_callback(self, retry_handler):
        """Test retry with callback."""
        retry_attempts = []

        def on_retry(attempt, error):
            retry_attempts.append((attempt, str(error)))

        retry_handler.on_retry = on_retry

        async def operation():
            if len(retry_attempts) < 2:
                raise SandboxError(f"Attempt {len(retry_attempts) + 1}")
            return "success"

        result = await retry_handler.execute(operation)
        assert result == "success"
        assert len(retry_attempts) == 2
        assert retry_attempts[0] == (1, "Attempt 1")
        assert retry_attempts[1] == (2, "Attempt 2")


class TestCircuitBreaker:
    """Test circuit breaker functionality."""

    @pytest.fixture
    def circuit_breaker(self):
        """Create circuit breaker."""
        return CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=0.5,  # Short timeout for tests
            half_open_requests=1,
        )

    @pytest.mark.asyncio
    async def test_circuit_closed_success(self, circuit_breaker):
        """Test circuit breaker in closed state with success."""

        async def operation():
            return "success"

        result = await circuit_breaker.call(operation)
        assert result == "success"
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_opens_on_failures(self, circuit_breaker):
        """Test circuit opens after threshold failures."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            raise SandboxError("Failure")

        # First failures should go through
        for _i in range(3):
            with pytest.raises(SandboxError):
                await circuit_breaker.call(operation)

        assert circuit_breaker.state == CircuitBreakerState.OPEN
        assert circuit_breaker.failure_count == 3

        # Next call should fail immediately without calling operation
        with pytest.raises(ProviderError) as exc_info:
            await circuit_breaker.call(operation)

        assert "Circuit breaker is OPEN" in str(exc_info.value)
        assert call_count == 3  # Operation not called when open

    @pytest.mark.asyncio
    async def test_circuit_recovery(self, circuit_breaker):
        """Test circuit breaker recovery to half-open state."""

        async def failing_operation():
            raise SandboxError("Failure")

        async def success_operation():
            return "success"

        # Open the circuit
        for _ in range(3):
            with pytest.raises(SandboxError):
                await circuit_breaker.call(failing_operation)

        assert circuit_breaker.state == CircuitBreakerState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.6)

        # Should transition to half-open and allow one request
        result = await circuit_breaker.call(success_operation)
        assert result == "success"
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_half_open_failure(self, circuit_breaker):
        """Test circuit returns to open from half-open on failure."""

        async def failing_operation():
            raise SandboxError("Failure")

        # Open the circuit
        for _ in range(3):
            with pytest.raises(SandboxError):
                await circuit_breaker.call(failing_operation)

        assert circuit_breaker.state == CircuitBreakerState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.6)

        # Failure in half-open should return to open
        with pytest.raises(SandboxError):
            await circuit_breaker.call(failing_operation)

        assert circuit_breaker.state == CircuitBreakerState.OPEN

    @pytest.mark.asyncio
    async def test_circuit_breaker_reset(self, circuit_breaker):
        """Test manual reset of circuit breaker."""

        async def failing_operation():
            raise SandboxError("Failure")

        # Open the circuit
        for _ in range(3):
            with pytest.raises(SandboxError):
                await circuit_breaker.call(failing_operation)

        assert circuit_breaker.state == CircuitBreakerState.OPEN

        # Manual reset
        circuit_breaker.reset()
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_success_threshold(self, circuit_breaker):
        """Test success threshold in half-open state."""
        circuit_breaker.success_threshold = 2

        async def failing_operation():
            raise SandboxError("Failure")

        async def success_operation():
            return "success"

        # Open the circuit
        for _ in range(3):
            with pytest.raises(SandboxError):
                await circuit_breaker.call(failing_operation)

        # Wait for recovery
        await asyncio.sleep(0.6)

        # Need multiple successes to close
        result1 = await circuit_breaker.call(success_operation)
        assert result1 == "success"
        assert circuit_breaker.state == CircuitBreakerState.HALF_OPEN

        result2 = await circuit_breaker.call(success_operation)
        assert result2 == "success"
        assert circuit_breaker.state == CircuitBreakerState.CLOSED


class TestBackoffStrategies:
    """Test different backoff strategies."""

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test exponential backoff calculation."""
        backoff = ExponentialBackoff(base=2.0, initial=0.1, max_delay=10.0)

        delays = [backoff.get_delay(i) for i in range(5)]
        assert delays[0] == 0.1
        assert delays[1] == 0.2
        assert delays[2] == 0.4
        assert delays[3] == 0.8
        assert delays[4] == 1.6

    @pytest.mark.asyncio
    async def test_linear_backoff(self):
        """Test linear backoff calculation."""
        backoff = LinearBackoff(increment=0.5, initial=0.1, max_delay=2.0)

        delays = [backoff.get_delay(i) for i in range(5)]
        assert delays[0] == 0.1
        assert delays[1] == 0.6
        assert delays[2] == 1.1
        assert delays[3] == 1.6
        assert delays[4] == 2.0  # Capped at max

    @pytest.mark.asyncio
    async def test_backoff_with_jitter(self):
        """Test backoff with jitter."""
        backoff = ExponentialBackoff(base=2.0, initial=1.0, jitter=True)

        # With jitter, delays should vary
        delays = [backoff.get_delay(1) for _ in range(10)]
        assert min(delays) < 2.0  # Some should be less than base
        assert max(delays) <= 2.0  # None should exceed base
        assert len(set(delays)) > 1  # Should have variation


class TestWithRetryDecorator:
    """Test the with_retry decorator."""

    @pytest.mark.asyncio
    async def test_decorator_basic(self):
        """Test basic decorator functionality."""
        call_count = 0

        @with_retry(max_retries=2, initial_delay=0.01)
        async def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise SandboxError("Temporary failure")
            return "success"

        result = await flaky_operation()
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_with_args(self):
        """Test decorator with function arguments."""

        @with_retry(max_retries=3, initial_delay=0.01)
        async def operation_with_args(value, should_fail=False):
            if should_fail:
                raise SandboxError("Failed")
            return f"Result: {value}"

        result = await operation_with_args("test", should_fail=False)
        assert result == "Result: test"

        with pytest.raises(SandboxError):
            await operation_with_args("test", should_fail=True)

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_metadata(self):
        """Test decorator preserves function metadata."""

        @with_retry(max_retries=1)
        async def documented_function():
            """This function has documentation."""
            return "result"

        assert documented_function.__doc__ == "This function has documentation."
        assert documented_function.__name__ == "documented_function"


class TestIntegratedRetryAndCircuitBreaker:
    """Test retry and circuit breaker working together."""

    @pytest.mark.asyncio
    async def test_retry_with_circuit_breaker(self):
        """Test retry handler with circuit breaker."""
        circuit_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0.5)

        retry_handler = RetryHandler(
            RetryConfig(max_retries=3, initial_delay=0.01, allow_additional_attempt=True)
        )

        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                raise SandboxError(f"Failure {call_count}")
            return "success"

        # Wrap operation with circuit breaker
        async def protected_operation():
            return await circuit_breaker.call(operation)

        # First attempt will fail and open circuit after 2 failures
        with pytest.raises(ProviderError) as exc_info:
            await retry_handler.execute(protected_operation)

        assert "Circuit breaker is OPEN" in str(exc_info.value)
        assert circuit_breaker.state == CircuitBreakerState.OPEN

        # Wait for recovery
        await asyncio.sleep(0.6)

        # Should eventually succeed
        call_count = 0  # Reset for clean test
        result = await retry_handler.execute(operation)
        assert result == "success"
