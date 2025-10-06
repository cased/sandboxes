"""High-level Sandbox interface for simplified usage."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from .base import ExecutionResult, SandboxConfig
from .base import Sandbox as BaseSandbox
from .manager import SandboxManager
from .providers import CloudflareProvider, DaytonaProvider, E2BProvider, ModalProvider


class _SandboxAsyncContextManager:
    """Helper to make Sandbox.create() work with both await and async with."""

    def __init__(self, create_coro):
        """Initialize with coroutine for creating sandbox."""
        self._create_coro = create_coro
        self._sandbox: Sandbox | None = None

    def __await__(self):
        """Allow awaiting directly: sandbox = await Sandbox.create()"""
        return self._create_coro.__await__()

    async def __aenter__(self) -> Sandbox:
        """Create sandbox on entry: async with Sandbox.create() as sandbox:"""
        self._sandbox = await self._create_coro
        return self._sandbox

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Destroy sandbox on exit."""
        if self._sandbox:
            await self._sandbox.destroy()


class Sandbox:
    """High-level interface for sandbox operations with automatic provider management."""

    _manager: SandboxManager | None = None
    _auto_configured = False

    def __init__(self, sandbox: BaseSandbox, provider_name: str):
        """Initialize with a base sandbox instance."""
        self._sandbox = sandbox
        self._provider_name = provider_name
        self.id = sandbox.id
        self.state = sandbox.state
        self.labels = sandbox.labels
        self.metadata = sandbox.metadata

    @classmethod
    def _ensure_manager(cls) -> SandboxManager:
        """Ensure the manager is configured."""
        if cls._manager is None:
            cls._manager = SandboxManager()

        if not cls._auto_configured:
            cls._auto_configure()
            cls._auto_configured = True

        return cls._manager

    @classmethod
    def _auto_configure(cls) -> None:
        """
        Auto-configure available providers based on environment variables.

        Providers are registered in priority order:
        1. Daytona
        2. E2B
        3. Modal
        4. Cloudflare (experimental)

        The first registered provider becomes the default unless explicitly set.
        Users can override with Sandbox.configure(default_provider="...").
        """
        manager = cls._manager

        # Try to register Daytona (priority 1)
        if os.getenv("DAYTONA_API_KEY"):
            try:
                manager.register_provider("daytona", DaytonaProvider, {})
                print("✓ Registered Daytona provider")
            except Exception:
                pass

        # Try to register E2B (priority 2)
        if os.getenv("E2B_API_KEY"):
            try:
                manager.register_provider("e2b", E2BProvider, {})
                print("✓ Registered E2B provider")
            except Exception:
                pass

        # Try to register Modal (priority 3)
        if os.path.exists(os.path.expanduser("~/.modal.toml")) or os.getenv("MODAL_TOKEN_ID"):
            try:
                manager.register_provider("modal", ModalProvider, {})
                print("✓ Registered Modal provider")
            except Exception:
                pass

        # Try to register Cloudflare (priority 4 - experimental)
        base_url = os.getenv("CLOUDFLARE_SANDBOX_BASE_URL")
        api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        if base_url and api_token:
            try:
                manager.register_provider(
                    "cloudflare",
                    CloudflareProvider,
                    {
                        "base_url": base_url,
                        "api_token": api_token,
                        "account_id": os.getenv("CLOUDFLARE_ACCOUNT_ID"),
                    },
                )
                print("✓ Registered Cloudflare provider (experimental)")
            except Exception:
                pass

    @classmethod
    def configure(
        cls,
        *,
        e2b_api_key: str | None = None,
        modal_token: str | None = None,
        daytona_api_key: str | None = None,
        cloudflare_config: dict[str, str] | None = None,
        default_provider: str | None = None,
    ) -> None:
        """
        Manually configure providers.

        Example:
            Sandbox.configure(
                e2b_api_key="...",
                default_provider="e2b"
            )
        """
        manager = cls._ensure_manager()

        if e2b_api_key:
            manager.register_provider("e2b", E2BProvider, {"api_key": e2b_api_key})

        if modal_token:
            # Modal configuration would go here
            manager.register_provider("modal", ModalProvider, {})

        if daytona_api_key:
            manager.register_provider("daytona", DaytonaProvider, {"api_key": daytona_api_key})

        if cloudflare_config:
            manager.register_provider("cloudflare", CloudflareProvider, cloudflare_config)

        if default_provider:
            manager.default_provider = default_provider

    @classmethod
    async def _create_impl(
        cls,
        provider: str | None = None,
        fallback: list[str] | None = None,
        labels: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int = 300,
        image: str | None = None,
        **kwargs: Any,
    ) -> Sandbox:
        """Internal implementation of sandbox creation."""
        manager = cls._ensure_manager()

        # Build config
        config = SandboxConfig(
            labels=labels,
            env_vars=env_vars,
            timeout_seconds=timeout,
            image=image,
            **kwargs,
        )

        # Create sandbox with fallback support
        base_sandbox = await manager.create_sandbox(
            config=config, provider=provider, fallback_providers=fallback
        )

        # Get the provider name that was actually used
        provider_name = base_sandbox.provider

        return cls(base_sandbox, provider_name)

    @classmethod
    def create(
        cls,
        *,
        provider: str | None = None,
        fallback: list[str] | None = None,
        labels: dict[str, str] | None = None,
        env_vars: dict[str, str] | None = None,
        timeout: int = 300,
        image: str | None = None,
        **kwargs: Any,
    ) -> _SandboxAsyncContextManager:
        """
        Create a new sandbox with automatic provider selection.

        Can be used with both await and async with:
            # Direct await
            sandbox = await Sandbox.create()

            # Async context manager (auto-cleanup)
            async with Sandbox.create() as sandbox:
                result = await sandbox.execute("echo hello")

        Args:
            provider: Preferred provider name (optional)
            fallback: List of fallback providers (optional)
            labels: Labels for sandbox identification
            env_vars: Environment variables
            timeout: Execution timeout in seconds
            image: Custom image/template
            **kwargs: Additional provider-specific config

        Returns:
            An awaitable async context manager for the sandbox

        Example:
            # Auto-detect and use first available provider
            sandbox = await Sandbox.create()

            # Use specific provider with fallbacks
            async with Sandbox.create(
                provider="e2b",
                fallback=["modal", "cloudflare"],
                labels={"task": "test"}
            ) as sandbox:
                result = await sandbox.execute("python script.py")

            # Or without context manager (manual cleanup)
            sandbox = await Sandbox.create()
            result = await sandbox.execute("python script.py")
            await sandbox.destroy()
        """
        # Create coroutine for sandbox creation
        coro = cls._create_impl(
            provider=provider,
            fallback=fallback,
            labels=labels,
            env_vars=env_vars,
            timeout=timeout,
            image=image,
            **kwargs,
        )

        # Return wrapper that supports both await and async with
        return _SandboxAsyncContextManager(coro)

    @classmethod
    async def find(
        cls,
        labels: dict[str, str],
        provider: str | None = None,
    ) -> Sandbox | None:
        """
        Find an existing sandbox by labels.

        Args:
            labels: Labels to search for
            provider: Specific provider to search (optional)

        Returns:
            Sandbox instance if found, None otherwise
        """
        manager = cls._ensure_manager()

        providers_to_check = [provider] if provider else list(manager.providers.keys())

        for provider_name in providers_to_check:
            try:
                provider_obj = manager.get_provider(provider_name)
                base_sandbox = await provider_obj.find_sandbox(labels)
                if base_sandbox:
                    return cls(base_sandbox, provider_name)
            except Exception:
                continue

        return None

    @classmethod
    async def get_or_create(
        cls,
        labels: dict[str, str],
        **create_kwargs: Any,
    ) -> Sandbox:
        """
        Get existing sandbox or create new one.

        Args:
            labels: Labels for sandbox identification
            **create_kwargs: Arguments passed to create() if needed

        Returns:
            Existing or newly created sandbox
        """
        # Try to find existing
        existing = await cls.find(labels)
        if existing:
            return existing

        # Create new with labels
        return await cls.create(labels=labels, **create_kwargs)

    async def execute(
        self,
        command: str,
        env_vars: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """
        Execute a command in the sandbox.

        Args:
            command: Command to execute
            env_vars: Additional environment variables
            timeout: Execution timeout in seconds

        Returns:
            Execution result with stdout, stderr, and exit code
        """
        manager = self._ensure_manager()
        provider = manager.get_provider(self._provider_name)
        return await provider.execute_command(self.id, command, timeout, env_vars)

    async def execute_many(
        self,
        commands: list[str],
        stop_on_error: bool = True,
        **kwargs: Any,
    ) -> list[ExecutionResult]:
        """Execute multiple commands in sequence."""
        manager = self._ensure_manager()
        provider = manager.get_provider(self._provider_name)
        return await provider.execute_commands(
            self.id, commands, stop_on_error=stop_on_error, **kwargs
        )

    async def stream(
        self,
        command: str,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream command execution output."""
        manager = self._ensure_manager()
        provider = manager.get_provider(self._provider_name)
        async for chunk in provider.stream_execution(self.id, command, **kwargs):
            yield chunk

    async def upload(
        self,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Upload a file to the sandbox."""
        manager = self._ensure_manager()
        provider = manager.get_provider(self._provider_name)
        return await provider.upload_file(self.id, local_path, remote_path)

    async def download(
        self,
        remote_path: str,
        local_path: str,
    ) -> bool:
        """Download a file from the sandbox."""
        manager = self._ensure_manager()
        provider = manager.get_provider(self._provider_name)
        return await provider.download_file(self.id, remote_path, local_path)

    async def destroy(self) -> bool:
        """Destroy this sandbox."""
        manager = self._ensure_manager()
        provider = manager.get_provider(self._provider_name)
        return await provider.destroy_sandbox(self.id)

    async def __aenter__(self) -> Sandbox:
        """Support async context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up sandbox on exit."""
        await self.destroy()

    def __repr__(self) -> str:
        return f"<Sandbox id={self.id} provider={self._provider_name} state={self.state}>"


# Convenience functions for even simpler usage
async def run(
    command: str,
    *,
    provider: str | None = None,
    **kwargs: Any,
) -> ExecutionResult:
    """
    Quick one-shot command execution.

    Example:
        result = await run("echo hello")
        print(result.stdout)
    """
    sandbox = await Sandbox.create(provider=provider, **kwargs)
    async with sandbox:
        return await sandbox.execute(command)


async def run_many(
    commands: list[str],
    *,
    provider: str | None = None,
    **kwargs: Any,
) -> list[ExecutionResult]:
    """
    Execute multiple commands in a temporary sandbox.

    Example:
        results = await run_many([
            "pip install requests",
            "python -c 'import requests; print(requests.__version__)'"
        ])
    """
    sandbox = await Sandbox.create(provider=provider, **kwargs)
    async with sandbox:
        return await sandbox.execute_many(commands)
