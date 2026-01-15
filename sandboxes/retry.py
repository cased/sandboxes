"""Retry and error handling utilities for sandbox operations."""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, TypeVar

from .exceptions import (
    ProviderError,
    SandboxAuthenticationError,
    SandboxNotFoundError,
    SandboxTimeoutError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitBreakerState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True
    timeout: float | None = None
    should_retry: Callable[[Exception, int], bool] | None = None
    on_retry: Callable[[Exception, int], None] | None = None
    circuit_breaker: CircuitBreaker | None = None
    allow_additional_attempt: bool = False

    # Errors to retry
    retryable_errors: tuple[type[Exception], ...] | None = None

    # Errors to never retry
    non_retryable_errors: tuple[type[Exception], ...] = (
        SandboxAuthenticationError,
        SandboxNotFoundError,
    )

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.initial_delay < 0 or self.max_delay < 0:
            raise ValueError("Delays must be non-negative")
        if self.exponential_base <= 0:
            raise ValueError("exponential_base must be positive")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")


class RetryHandler:
    """Handles retry logic with exponential backoff and jitter."""

    def __init__(self, config: RetryConfig | None = None):
        """Initialize retry handler."""
        self.config = config or RetryConfig()
        self.should_retry: Callable[[Exception, int], bool] | None = None
        self.on_retry = self.config.on_retry
        self.timeout = self.config.timeout
        self.circuit_breaker = self.config.circuit_breaker
        self.allow_additional_attempt = self.config.allow_additional_attempt

    def calculate_delay(self, attempt_number: int) -> float:
        """Calculate delay for the given (1-based) attempt number."""
        base_delay = self.config.initial_delay * (
            self.config.exponential_base ** max(0, attempt_number - 1)
        )
        delay = min(base_delay, self.config.max_delay)

        if self.config.jitter and delay > 0:
            delay *= 1 + random.random() * 0.25
            delay = min(delay, self.config.max_delay)

        return delay

    def _should_retry(self, error: Exception, attempt_index: int) -> bool:
        """Determine if operation should be retried (attempt_index is zero-based)."""
        effective_retries = self.config.max_retries + (1 if self.allow_additional_attempt else 0)
        if attempt_index >= effective_retries:
            return False

        if isinstance(error, self.config.non_retryable_errors):
            return False

        predicate = self.should_retry
        if predicate:
            try:
                sig = inspect.signature(predicate)
                if len(sig.parameters) <= 1:
                    decision = predicate(error)
                else:
                    decision = predicate(error, attempt_index)
            except Exception as predicate_error:
                logger.error(f"Retry predicate raised an error: {predicate_error}")
                return False
            return bool(decision)

        if self.config.should_retry:
            try:
                sig = inspect.signature(self.config.should_retry)
                if len(sig.parameters) <= 1:
                    config_decision = self.config.should_retry(error)
                else:
                    config_decision = self.config.should_retry(error, attempt_index)
            except Exception as predicate_error:
                logger.error(f"Retry predicate raised an error: {predicate_error}")
                config_decision = None

            if config_decision is True:
                return True

        if self.config.retryable_errors:
            return isinstance(error, self.config.retryable_errors)

        return True

    async def execute(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Execute function with retry logic (alias for execute_with_retry)."""
        return await self.execute_with_retry(func, *args, **kwargs)

    async def execute_with_retry(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Execute function with retry logic."""
        last_error = None

        max_attempts = self.config.max_retries + 1 + (1 if self.allow_additional_attempt else 0)
        attempt_index = 0

        while attempt_index < max_attempts:
            try:
                return await self._execute_attempt(func, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_error = exc

                if not self._should_retry(exc, attempt_index):
                    logger.warning(f"Non-retryable error in {func.__name__}: {exc}")
                    raise

                self._handle_retry_callback(exc, attempt_index + 1)

                delay = self.calculate_delay(attempt_index + 1)
                if (
                    self.circuit_breaker
                    and isinstance(exc, ProviderError)
                    and "Circuit breaker is open" in str(exc)
                ):
                    delay = max(delay, self.circuit_breaker.recovery_timeout)
                logger.info(
                    f"Retry {attempt_index + 1}/{self.config.max_retries} for {func.__name__} "
                    f"after {delay:.2f}s. Error: {exc}"
                )
                await asyncio.sleep(delay)

                consume_retry = True
                if (
                    self.circuit_breaker
                    and isinstance(exc, ProviderError)
                    and "Circuit breaker is open" in str(exc)
                ):
                    consume_retry = False

                if consume_retry:
                    attempt_index += 1
                continue

        logger.error(f"All {self.config.max_retries} retries failed for {func.__name__}")
        raise last_error

    async def _execute_attempt(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Execute a single attempt, respecting timeout and circuit breaker."""

        if self.circuit_breaker:
            return await self.circuit_breaker.call(lambda: self._invoke(func, *args, **kwargs))

        return await self._invoke(func, *args, **kwargs)

    async def _invoke(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Invoke target function and await if necessary."""

        result = func(*args, **kwargs)

        if inspect.isawaitable(result):
            if self.timeout:
                return await asyncio.wait_for(result, timeout=self.timeout)
            return await result

        return result

    def _handle_retry_callback(self, error: Exception, attempt_number: int) -> None:
        """Invoke retry callback safely."""
        callback = self.on_retry
        if not callback:
            return

        try:
            sig = inspect.signature(callback)
            if len(sig.parameters) <= 1:
                callback(attempt_number)
            else:
                callback(attempt_number, error)
        except Exception as callback_error:  # noqa: BLE001
            logger.error(f"Retry callback raised an error: {callback_error}")


def with_retry(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    retryable_errors: tuple[type[Exception], ...] | None = None,
    non_retryable_errors: tuple[type[Exception], ...] | None = None,
) -> Callable:
    """
    Decorator to add retry logic to async functions.

    Args:
        max_attempts: Maximum number of retry attempts
        retryable_errors: Tuple of exceptions to retry on
        non_retryable_errors: Tuple of exceptions to never retry

    Example:
        @with_retry(max_attempts=3)
        async def create_sandbox():
            # ... operation that might fail
    """

    def decorator(func: Callable) -> Callable:
        if inspect.isasyncgenfunction(func):
            return func

        @wraps(func)
        async def wrapper(*args, **kwargs):
            config = RetryConfig(max_retries=max_retries, initial_delay=initial_delay)

            if retryable_errors:
                config.retryable_errors = retryable_errors

            if non_retryable_errors:
                config.non_retryable_errors = non_retryable_errors

            handler = RetryHandler(config)
            return await handler.execute_with_retry(func, *args, **kwargs)

        return wrapper

    return decorator


class ExponentialBackoff:
    """Exponential backoff strategy."""

    def __init__(
        self, base: float = 2.0, initial: float = 1.0, max_delay: float = 60.0, jitter: bool = False
    ):
        self.base = base
        self.initial = initial
        self.max_delay = max_delay
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """Get delay for given attempt."""
        delay = min(self.initial * (self.base**attempt), self.max_delay)
        if self.jitter:
            delay *= random.random()
        return delay


class LinearBackoff:
    """Linear backoff strategy."""

    def __init__(self, increment: float = 1.0, initial: float = 1.0, max_delay: float = 60.0):
        self.increment = increment
        self.initial = initial
        self.max_delay = max_delay

    def get_delay(self, attempt: int) -> float:
        """Get delay for given attempt."""
        return min(self.initial + (self.increment * attempt), self.max_delay)


class CircuitBreaker:
    """Circuit breaker pattern for failing services."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 1,
        half_open_requests: int = 1,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if success_threshold <= 0:
            raise ValueError("success_threshold must be positive")
        if half_open_requests <= 0:
            raise ValueError("half_open_requests must be positive")

        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.half_open_requests = half_open_requests

        self.state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.last_failure_time: float = 0.0

        self._half_open_in_flight = 0
        self._lock = asyncio.Lock()

    def is_open(self) -> bool:
        return self.state == CircuitBreakerState.OPEN

    def is_half_open(self) -> bool:
        return self.state == CircuitBreakerState.HALF_OPEN

    def record_success(self) -> None:
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            self.consecutive_successes += 1
            if self.success_count >= self.success_threshold:
                self._close()
        else:
            self.failure_count = 0
            self.consecutive_failures = 0
            self.consecutive_successes += 1

    def record_failure(self) -> None:
        self.failure_count += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.success_count = 0
        self.last_failure_time = self._current_time()

        if (
            self.state == CircuitBreakerState.HALF_OPEN
            or self.failure_count >= self.failure_threshold
        ):
            self._open()

    def should_attempt(self) -> bool:
        if self.state == CircuitBreakerState.CLOSED:
            return True

        if self.state == CircuitBreakerState.OPEN:
            if self._current_time() - self.last_failure_time >= self.recovery_timeout:
                self._half_open()
                return True
            return False

        # HALF_OPEN
        return self._half_open_in_flight < self.half_open_requests

    async def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        half_open_ticket = False

        async with self._lock:
            if self.state == CircuitBreakerState.OPEN:
                if self._current_time() - self.last_failure_time >= self.recovery_timeout:
                    self._half_open()
                else:
                    raise ProviderError("Circuit breaker is open / Circuit breaker is OPEN")

            if self.state == CircuitBreakerState.HALF_OPEN:
                if self._half_open_in_flight >= self.half_open_requests:
                    raise ProviderError("Circuit breaker is HALF_OPEN")
                self._half_open_in_flight += 1
                half_open_ticket = True

        try:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception:  # noqa: BLE001
            async with self._lock:
                if half_open_ticket:
                    self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
                self._record_failure_locked()
            raise
        else:
            async with self._lock:
                if half_open_ticket:
                    self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
                self._record_success_locked()
            return result

    def reset(self) -> None:
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.last_failure_time = 0.0
        self._half_open_in_flight = 0

    def _record_failure_locked(self) -> None:
        self.failure_count += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.success_count = 0
        self.last_failure_time = self._current_time()

        if self.state == CircuitBreakerState.HALF_OPEN or (
            self.state == CircuitBreakerState.CLOSED
            and self.failure_count >= self.failure_threshold
        ):
            self._open()

    def _record_success_locked(self) -> None:
        self.failure_count = 0
        self.consecutive_failures = 0

        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            self.consecutive_successes += 1
            if self.success_count >= self.success_threshold:
                self._close()
        else:
            self.consecutive_successes += 1

    def _open(self) -> None:
        consecutive = self.consecutive_failures
        self.state = CircuitBreakerState.OPEN
        self.success_count = 0
        self.consecutive_successes = 0
        self._half_open_in_flight = 0
        self.last_failure_time = self._current_time()
        logger.warning(f"Circuit breaker opened after {consecutive} failures")
        self.consecutive_failures = 0

    def _half_open(self) -> None:
        self.state = CircuitBreakerState.HALF_OPEN
        self.success_count = 0
        self.consecutive_successes = 0
        self.failure_count = 0
        self.consecutive_failures = 0
        self._half_open_in_flight = 0
        logger.info("Circuit breaker half-open, testing recovery")

    def _close(self) -> None:
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.consecutive_failures = 0
        self.success_count = 0
        self.consecutive_successes = 0
        self._half_open_in_flight = 0
        logger.info("Circuit breaker closed, service recovered")

    def _current_time(self) -> float:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        return loop.time()


class ErrorAggregator:
    """Aggregate and analyze errors for better debugging."""

    def __init__(self, window_size: int = 100):
        """Initialize error aggregator."""
        self.window_size = window_size
        self.errors: list = []
        self.error_counts: dict = {}

    def record_error(self, error: Exception, context: dict | None = None):
        """Record an error with optional context."""
        error_info = {
            "type": type(error).__name__,
            "message": str(error),
            "timestamp": asyncio.get_event_loop().time(),
            "context": context or {},
        }

        self.errors.append(error_info)

        # Keep window size
        if len(self.errors) > self.window_size:
            self.errors.pop(0)

        # Update counts
        error_type = error_info["type"]
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1

    def get_summary(self) -> dict:
        """Get error summary statistics."""
        if not self.errors:
            return {"total": 0, "types": {}}

        return {
            "total": len(self.errors),
            "types": self.error_counts,
            "recent": self.errors[-10:],  # Last 10 errors
            "most_common": (
                max(self.error_counts, key=self.error_counts.get) if self.error_counts else None
            ),
        }

    def clear(self):
        """Clear error history."""
        self.errors.clear()
        self.error_counts.clear()


async def with_timeout(coro: Any, timeout: float, error_message: str | None = None) -> Any:
    """
    Execute coroutine with timeout.

    Args:
        coro: Coroutine to execute
        timeout: Timeout in seconds
        error_message: Custom error message

    Raises:
        SandboxTimeoutError: If operation times out
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError as e:
        message = error_message or f"Operation timed out after {timeout} seconds"
        raise SandboxTimeoutError(message) from e


class RateLimiter:
    """Rate limiter for API calls."""

    def __init__(self, rate: int, period: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            rate: Maximum calls allowed
            period: Time period in seconds
        """
        self.rate = rate
        self.period = period
        self.semaphore = asyncio.Semaphore(rate)
        self.reset_task = None

    async def __aenter__(self):
        """Acquire rate limit permit."""
        await self.semaphore.acquire()
        if self.reset_task is None:
            self.reset_task = asyncio.create_task(self._reset_loop())

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release is handled by reset loop."""
        pass

    async def _reset_loop(self):
        """Periodically release permits back to the semaphore."""
        import contextlib

        while True:
            await asyncio.sleep(self.period)
            # Release a permit back to the semaphore (up to max rate)
            # ValueError raised if semaphore already at max
            with contextlib.suppress(ValueError):
                self.semaphore.release()
