"""Tests for exception classes."""

from sandboxes.exceptions import (
    ProviderError,
    SandboxAuthenticationError,
    SandboxError,
    SandboxNotFoundError,
    SandboxQuotaError,
    SandboxStateError,
    SandboxTimeoutError,
)


class TestExceptions:
    """Test exception hierarchy and behavior."""

    def test_sandbox_error_base(self):
        """Test base SandboxError."""
        error = SandboxError("Test error")
        assert str(error) == "Test error"
        assert isinstance(error, Exception)

    def test_provider_error(self):
        """Test ProviderError inherits from SandboxError."""
        error = ProviderError("Provider failed")
        assert str(error) == "Provider failed"
        assert isinstance(error, SandboxError)
        assert isinstance(error, Exception)

    def test_sandbox_not_found_error(self):
        """Test SandboxNotFoundError with sandbox ID."""
        sandbox_id = "sandbox-123"
        error = SandboxNotFoundError(f"Sandbox {sandbox_id} not found")
        assert "sandbox-123" in str(error)
        assert isinstance(error, SandboxError)

    def test_sandbox_timeout_error(self):
        """Test SandboxTimeoutError with timeout duration."""
        timeout = 30
        error = SandboxTimeoutError(f"Operation timed out after {timeout}s")
        assert "30s" in str(error)
        assert isinstance(error, SandboxError)

    def test_sandbox_state_error(self):
        """Test SandboxStateError for invalid state transitions."""
        error = SandboxStateError("Cannot execute in DESTROYED state")
        assert "DESTROYED" in str(error)
        assert isinstance(error, SandboxError)

    def test_sandbox_quota_error(self):
        """Test SandboxQuotaError for quota exceeded."""
        error = SandboxQuotaError("Monthly quota of 100 sandboxes exceeded")
        assert "quota" in str(error).lower()
        assert isinstance(error, SandboxError)

    def test_sandbox_authentication_error(self):
        """Test SandboxAuthenticationError inherits from ProviderError."""
        error = SandboxAuthenticationError("Invalid API key")
        assert "API key" in str(error)
        assert isinstance(error, ProviderError)
        assert isinstance(error, SandboxError)

    def test_exception_with_cause(self):
        """Test exception chaining with __cause__."""
        original = ValueError("Original error")
        error = SandboxError("Wrapper error")
        error.__cause__ = original

        assert error.__cause__ is original
        assert isinstance(error.__cause__, ValueError)

    def test_exception_with_context(self):
        """Test exception chaining with context."""
        try:
            try:
                raise ValueError("Original error")
            except ValueError:
                raise SandboxError("Wrapper error")
        except SandboxError as e:
            assert e.__context__ is not None
            assert isinstance(e.__context__, ValueError)

    def test_exception_hierarchy(self):
        """Test complete exception hierarchy."""
        # All custom exceptions inherit from SandboxError
        exceptions = [
            ProviderError,
            SandboxNotFoundError,
            SandboxTimeoutError,
            SandboxStateError,
            SandboxQuotaError,
        ]

        for exc_class in exceptions:
            instance = exc_class("Test")
            assert isinstance(instance, SandboxError)
            assert isinstance(instance, Exception)

        # SandboxAuthenticationError inherits from ProviderError
        auth_error = SandboxAuthenticationError("Auth failed")
        assert isinstance(auth_error, ProviderError)
        assert isinstance(auth_error, SandboxError)

    def test_exception_args(self):
        """Test exceptions preserve arguments."""
        error = SandboxError("Message", 404, {"detail": "Not found"})
        assert error.args == ("Message", 404, {"detail": "Not found"})
        assert error.args[0] == "Message"
        assert error.args[1] == 404
        assert error.args[2] == {"detail": "Not found"}

    def test_exception_repr(self):
        """Test exception representation."""
        error = SandboxNotFoundError("Sandbox xyz not found")
        repr_str = repr(error)
        assert "SandboxNotFoundError" in repr_str
        assert "xyz" in repr_str
