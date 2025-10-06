"""Tests for provider validation."""

import pytest

from sandboxes.constants import VALID_PROVIDERS, validate_provider, validate_providers
from sandboxes.exceptions import ProviderError


class TestValidateProvider:
    """Test validate_provider function."""

    def test_valid_providers(self):
        """Test validation passes for valid providers."""
        for provider in VALID_PROVIDERS:
            validate_provider(provider, allow_none=False)

    def test_none_allowed(self):
        """Test None is allowed when allow_none=True."""
        validate_provider(None, allow_none=True)

    def test_none_not_allowed(self):
        """Test None raises error when allow_none=False."""
        with pytest.raises(ProviderError, match="Provider cannot be None"):
            validate_provider(None, allow_none=False)

    def test_invalid_provider(self):
        """Test invalid provider raises error."""
        with pytest.raises(ProviderError, match="Invalid provider: 'invalid'"):
            validate_provider("invalid", allow_none=False)

    def test_invalid_provider_shows_valid_options(self):
        """Test error message shows valid provider options."""
        with pytest.raises(ProviderError) as exc_info:
            validate_provider("invalid", allow_none=False)

        error_msg = str(exc_info.value)
        assert "cloudflare" in error_msg
        assert "daytona" in error_msg
        assert "e2b" in error_msg
        assert "modal" in error_msg

    def test_case_sensitive(self):
        """Test provider names are case-sensitive."""
        with pytest.raises(ProviderError):
            validate_provider("E2B", allow_none=False)

        with pytest.raises(ProviderError):
            validate_provider("Modal", allow_none=False)


class TestValidateProviders:
    """Test validate_providers function."""

    def test_valid_providers_list(self):
        """Test validation passes for list of valid providers."""
        validate_providers(["e2b", "modal", "daytona"], allow_none=False)

    def test_empty_list(self):
        """Test empty list is valid."""
        validate_providers([], allow_none=False)

    def test_none_list(self):
        """Test None list is valid."""
        validate_providers(None, allow_none=False)

    def test_list_with_none_allowed(self):
        """Test list with None values when allow_none=True."""
        validate_providers(["e2b", None, "modal"], allow_none=True)

    def test_list_with_none_not_allowed(self):
        """Test list with None values raises error when allow_none=False."""
        with pytest.raises(ProviderError, match="Provider cannot be None"):
            validate_providers(["e2b", None, "modal"], allow_none=False)

    def test_invalid_provider_in_list(self):
        """Test invalid provider in list raises error."""
        with pytest.raises(ProviderError, match="Invalid provider"):
            validate_providers(["e2b", "invalid", "modal"], allow_none=False)

    def test_all_invalid(self):
        """Test all invalid providers raises error."""
        with pytest.raises(ProviderError):
            validate_providers(["bad1", "bad2"], allow_none=False)


class TestIntegrationWithManager:
    """Test validation integration with Manager."""

    def test_manager_get_provider_validates(self):
        """Test Manager.get_provider validates provider names."""
        from sandboxes.manager import SandboxManager

        manager = SandboxManager()

        with pytest.raises(ProviderError, match="Invalid provider"):
            manager.get_provider("invalid")

    @pytest.mark.asyncio
    async def test_manager_create_sandbox_validates(self):
        """Test Manager.create_sandbox validates provider names."""
        from sandboxes import SandboxConfig
        from sandboxes.manager import SandboxManager

        manager = SandboxManager()

        with pytest.raises(ProviderError, match="Invalid provider"):
            await manager.create_sandbox(SandboxConfig(), provider="invalid")

    @pytest.mark.asyncio
    async def test_manager_create_sandbox_validates_fallback(self):
        """Test Manager.create_sandbox validates fallback provider names."""
        from sandboxes import SandboxConfig
        from sandboxes.manager import SandboxManager

        manager = SandboxManager()

        with pytest.raises(ProviderError, match="Invalid provider"):
            await manager.create_sandbox(
                SandboxConfig(), provider="e2b", fallback_providers=["modal", "invalid"]
            )


class TestIntegrationWithSandbox:
    """Test validation integration with Sandbox."""

    def test_sandbox_configure_validates_default_provider(self):
        """Test Sandbox.configure validates default_provider."""
        from sandboxes import Sandbox

        with pytest.raises(ProviderError, match="Invalid provider"):
            Sandbox.configure(default_provider="invalid")

    @pytest.mark.asyncio
    async def test_sandbox_create_validates_provider(self):
        """Test Sandbox.create validates provider."""
        from sandboxes import Sandbox

        with pytest.raises(ProviderError, match="Invalid provider"):
            await Sandbox.create(provider="invalid")

    @pytest.mark.asyncio
    async def test_sandbox_create_validates_fallback(self):
        """Test Sandbox.create validates fallback providers."""
        from sandboxes import Sandbox

        with pytest.raises(ProviderError, match="Invalid provider"):
            await Sandbox.create(fallback=["e2b", "invalid"])

    @pytest.mark.asyncio
    async def test_sandbox_find_validates_provider(self):
        """Test Sandbox.find validates provider."""
        from sandboxes import Sandbox

        with pytest.raises(ProviderError, match="Invalid provider"):
            await Sandbox.find(labels={"test": "true"}, provider="invalid")


class TestIntegrationWithCLI:
    """Test validation integration with CLI."""

    def test_cli_get_provider_validates(self):
        """Test CLI get_provider validates provider names."""
        from sandboxes.cli import get_provider

        with pytest.raises(SystemExit):
            get_provider("invalid")
