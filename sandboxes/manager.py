"""Unified sandbox manager for multi-provider support."""

import logging
from typing import Any

from .base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider
from .constants import validate_provider, validate_providers
from .exceptions import ProviderError

logger = logging.getLogger(__name__)


class SandboxManager:
    """
    Manages multiple sandbox providers and provides intelligent routing.

    Key features from comet:
    - Smart sandbox reuse based on labels
    - Session-based isolation
    - Automatic credential injection
    - Provider fallback on failures
    """

    def __init__(self, default_provider: str | None = None):
        """Initialize the sandbox manager."""
        self.providers: dict[str, SandboxProvider] = {}
        self.default_provider = default_provider
        self._provider_health: dict[str, bool] = {}

    def register_provider(
        self,
        name: str,
        provider_class: type[SandboxProvider],
        config: dict[str, Any] | None = None,
    ) -> None:
        """Register a sandbox provider."""
        config = config or {}
        try:
            provider = provider_class(**config)
            self.providers[name] = provider
            logger.info(f"Registered provider: {name}")

            if not self.default_provider:
                self.default_provider = name
        except Exception as e:
            logger.error(f"Failed to register provider {name}: {e}")
            raise ProviderError(f"Failed to register provider {name}: {e}") from e

    def get_provider(self, name: str | None = None) -> SandboxProvider:
        """Get a provider by name or the default provider."""
        name = name or self.default_provider
        if not name:
            raise ProviderError("No provider specified and no default provider set")

        # Validate provider name (only if not already registered, to allow custom/test providers)
        if name not in self.providers:
            validate_provider(name, allow_none=False)
            raise ProviderError(f"Provider '{name}' not registered")

        return self.providers[name]

    async def create_sandbox(
        self,
        config: SandboxConfig,
        provider: str | None = None,
        fallback_providers: list[str] | None = None,
    ) -> Sandbox:
        """
        Create a sandbox with automatic provider fallback.

        Args:
            config: Sandbox configuration
            provider: Preferred provider name
            fallback_providers: List of providers to try if primary fails
        """
        # Validate provider names (skip if already registered, to allow custom/test providers)
        if provider and provider not in self.providers:
            validate_provider(provider, allow_none=False)
        if fallback_providers:
            for fallback in fallback_providers:
                if fallback not in self.providers:
                    validate_provider(fallback, allow_none=False)

        providers_to_try = [provider] if provider else []

        if fallback_providers:
            providers_to_try.extend(fallback_providers)

        if not providers_to_try:
            providers_to_try = [self.default_provider]

        last_error = None
        for provider_name in providers_to_try:
            if not provider_name:
                continue

            try:
                provider = self.get_provider(provider_name)
                sandbox = await provider.create_sandbox(config)
                logger.info(f"Created sandbox {sandbox.id} with provider {provider_name}")
                return sandbox
            except Exception as e:
                logger.warning(f"Failed to create sandbox with {provider_name}: {e}")
                last_error = e
                continue

        raise ProviderError(f"Failed to create sandbox with any provider: {last_error}")

    async def get_or_create_sandbox(
        self,
        config: SandboxConfig,
        provider: str | None = None,
    ) -> Sandbox:
        """Get existing sandbox with matching labels or create new one."""
        provider_obj = self.get_provider(provider)
        return await provider_obj.get_or_create_sandbox(config)

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        provider: str | None = None,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
        mask_secrets: bool = True,
    ) -> ExecutionResult:
        """
        Execute command with automatic secret masking.

        Args:
            sandbox_id: Sandbox ID
            command: Command to execute
            provider: Provider name (will auto-detect if not specified)
            timeout: Command timeout in seconds
            env_vars: Additional environment variables
            mask_secrets: Whether to mask secrets in output
        """
        provider_obj = self.get_provider(provider)
        result = await provider_obj.execute_command(sandbox_id, command, timeout, env_vars)

        # Mask secrets in output if requested
        if mask_secrets and env_vars:
            result = self._mask_secrets(result, env_vars)

        return result

    def _mask_secrets(
        self,
        result: ExecutionResult,
        env_vars: dict[str, str],
    ) -> ExecutionResult:
        """Mask secret values in command output."""
        stdout = result.stdout
        stderr = result.stderr

        for key, value in env_vars.items():
            if (
                any(
                    secret_key in key.upper()
                    for secret_key in ["PASSWORD", "TOKEN", "KEY", "SECRET"]
                )
                and value
                and len(value) > 4
            ):
                masked_value = value[:2] + "*" * (len(value) - 4) + value[-2:]
                stdout = stdout.replace(value, masked_value)
                stderr = stderr.replace(value, masked_value)

        result.stdout = stdout
        result.stderr = stderr
        return result

    async def destroy_sandbox(
        self,
        sandbox_id: str,
        provider: str | None = None,
    ) -> bool:
        """Destroy a sandbox."""
        provider_obj = self.get_provider(provider)
        return await provider_obj.destroy_sandbox(sandbox_id)

    async def list_sandboxes(
        self,
        provider: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> list[Sandbox]:
        """List sandboxes from one or all providers."""
        if provider:
            provider_obj = self.get_provider(provider)
            return await provider_obj.list_sandboxes(labels)

        # List from all providers
        all_sandboxes = []
        for provider_name, provider_obj in self.providers.items():
            try:
                sandboxes = await provider_obj.list_sandboxes(labels)
                all_sandboxes.extend(sandboxes)
            except Exception as e:
                logger.warning(f"Failed to list sandboxes from {provider_name}: {e}")

        return all_sandboxes

    async def health_check(self, provider: str | None = None) -> dict[str, bool]:
        """Check health of one or all providers."""
        if provider:
            provider_obj = self.get_provider(provider)
            health = await provider_obj.health_check()
            return {provider: health}

        # Check all providers
        health_status = {}
        for provider_name, provider_obj in self.providers.items():
            try:
                health = await provider_obj.health_check()
                health_status[provider_name] = health
                self._provider_health[provider_name] = health
            except Exception as e:
                logger.error(f"Health check failed for {provider_name}: {e}")
                health_status[provider_name] = False
                self._provider_health[provider_name] = False

        return health_status

    async def cleanup_sandboxes(
        self,
        provider: str | None = None,
        labels: dict[str, str] | None = None,
        exclude_running: bool = True,
    ) -> int:
        """
        Clean up sandboxes based on criteria.

        Args:
            provider: Provider name or None for all
            labels: Labels to filter sandboxes
            exclude_running: Don't destroy running sandboxes

        Returns:
            Number of sandboxes destroyed
        """
        sandboxes = await self.list_sandboxes(provider, labels)
        destroyed_count = 0

        for sandbox in sandboxes:
            if exclude_running and sandbox.state.value == "running":
                continue

            try:
                # Extract provider from sandbox metadata
                sandbox_provider = sandbox.provider
                await self.destroy_sandbox(sandbox.id, sandbox_provider)
                destroyed_count += 1
                logger.info(f"Destroyed sandbox {sandbox.id}")
            except Exception as e:
                logger.error(f"Failed to destroy sandbox {sandbox.id}: {e}")

        return destroyed_count
