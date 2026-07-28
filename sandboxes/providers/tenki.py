"""Tenki Sandboxes provider implementation.

Tenki (https://tenki.cloud) is a managed microVM sandbox platform built for
agentic workloads. Its control plane is a Connect/gRPC API (``SandboxService``)
and command execution runs on a data plane reached over the sandbox gateway --
there is no plain REST surface. This provider is therefore a thin adapter over
the official ``tenki`` async SDK (``AsyncClient`` / ``AsyncSandbox``), which
speaks the real protocol, primes data-plane access, and handles auth.

Auth uses a Tenki API key supplied via ``TENKI_API_KEY`` (or passed explicitly).
Install the SDK with the ``tenki`` extra: ``pip install "cased-sandboxes[tenki]"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import warnings
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..base import (
    ExecutionResult,
    ProviderCapabilities,
    Sandbox,
    SandboxConfig,
    SandboxProvider,
    SandboxState,
)
from ..exceptions import (
    ProviderError,
    SandboxAuthenticationError,
    SandboxError,
    SandboxNotFoundError,
    SandboxTimeoutError,
)
from ..security import validate_download_path, validate_upload_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tenki import AsyncClient, AsyncSandbox

logger = logging.getLogger(__name__)

# Canonical Tenki session states (see SandboxSession.state) mapped to the
# library's provider-agnostic states.
_STATE_MAP = {
    "UNSPECIFIED": SandboxState.CREATING,
    "CREATING": SandboxState.CREATING,
    "PROVISIONING": SandboxState.CREATING,
    "STARTING": SandboxState.STARTING,
    "RESUMING": SandboxState.STARTING,
    "RUNNING": SandboxState.RUNNING,
    "PAUSING": SandboxState.STOPPING,
    "PAUSED": SandboxState.STOPPED,
    "STOPPED": SandboxState.STOPPED,
    "TERMINATING": SandboxState.STOPPING,
    "TERMINATED": SandboxState.TERMINATED,
    "FAILED": SandboxState.ERROR,
    "ERROR": SandboxState.ERROR,
}

# Keys accepted from SandboxConfig.provider_config and forwarded verbatim to
# AsyncClient.create(). Everything else in provider_config is ignored.
_CREATE_PASSTHROUGH = (
    "tags",
    "sticky",
    "idle_timeout_minutes",
    "snapshot_id",
    "disk_size_gb",
    "clone_repo_url",
    "enable_opencode",
    "allow_inbound",
    "allow_outbound",
    "pause_retention",
    "wait",
    "wait_for_runtime",
    "github_token",
    "ssh_authorized_keys",
    "volumes",
    # Added in tenki 0.5.x: build-from-template-spec and setup-phase inputs.
    "from_template_spec",
    "setup_env",
    "setup_secrets",
)


class TenkiProvider(SandboxProvider):
    """Interact with the Tenki Sandboxes platform via its official async SDK."""

    CAPABILITIES = ProviderCapabilities(
        persistent=True,
        snapshot=True,
        streaming=True,
        file_upload=True,
        interactive_shell=True,
    )

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        gateway_url: str | None = None,
        workspace_id: str | None = None,
        default_image: str | None = None,
        client: AsyncClient | None = None,
        **config,
    ) -> None:
        """Initialize the Tenki provider.

        Args:
            api_key: Tenki API key. Falls back to ``TENKI_API_KEY`` /
                ``TENKI_AUTH_TOKEN``.
            base_url: Control-plane API URL. Falls back to the SDK's resolution
                (``TENKI_API_ENDPOINT`` then the public endpoint).
            gateway_url: Sandbox gateway URL for the data plane. Falls back to
                the SDK's resolution (derived from ``base_url``).
            workspace_id: Default workspace scope for created/listed sandboxes.
                Falls back to ``TENKI_WORKSPACE_ID``.
            default_image: Default image/template launched when a config does
                not specify one. Falls back to ``TENKI_SANDBOX_IMAGE``.
            client: Pre-built ``AsyncClient`` (primarily for testing / reuse).
            **config: Additional configuration forwarded to the base class.
        """
        # tenki 0.5.x removed project scoping, so project_id would otherwise be
        # absorbed by **config and silently widen listings to the workspace.
        if "project_id" in config:
            config.pop("project_id")
            warnings.warn(
                "TenkiProvider(project_id=...) is ignored: tenki 0.5.x removed "
                "project scoping; use workspace_id instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        super().__init__(**config)

        self.api_key = api_key or os.getenv("TENKI_API_KEY") or os.getenv("TENKI_AUTH_TOKEN")
        if not self.api_key and client is None:
            raise ProviderError(
                "Tenki API key not provided (set TENKI_API_KEY or pass api_key=...)"
            )

        self.base_url = base_url or os.getenv("TENKI_API_ENDPOINT") or os.getenv("TENKI_API_URL")
        self.gateway_url = gateway_url or os.getenv("TENKI_SANDBOX_GATEWAY_URL")
        self.workspace_id = workspace_id or os.getenv("TENKI_WORKSPACE_ID")
        self.default_image = default_image or os.getenv("TENKI_SANDBOX_IMAGE")

        self._client = client
        # Cache AsyncSandbox handles by id so command/file ops reuse the primed
        # data-plane connection instead of re-fetching on every call.
        self._sandboxes: dict[str, AsyncSandbox] = {}
        # Ids currently being terminated; guards _resolve from returning or
        # re-caching a handle mid-destroy.
        self._destroying: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "tenki"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new Tenki sandbox (session)."""
        client = await self._get_client()

        image = (
            config.image
            or (config.provider_config.get("template") if config.provider_config else None)
            or (config.provider_config.get("image") if config.provider_config else None)
            or self.default_image
        )

        kwargs: dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "env": config.env_vars or None,
            # SandboxConfig.labels are key/value, so they map to Tenki metadata.
            "metadata": config.labels or None,
            "memory_mb": config.memory_mb,
            "cpu_cores": int(config.cpu_cores) if config.cpu_cores is not None else None,
        }
        if image:
            kwargs["image"] = image
        if config.timeout_seconds:
            kwargs["max_duration"] = config.timeout_seconds
        if config.provider_config:
            for key in _CREATE_PASSTHROUGH:
                if key in config.provider_config:
                    kwargs[key] = config.provider_config[key]

        kwargs = {k: v for k, v in kwargs.items() if v is not None}

        try:
            sb = await client.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise self._map_error(exc) from exc

        async with self._lock:
            self._sandboxes[sb.id] = sb

        sandbox = self._to_sandbox(sb, labels=config.labels or {})
        logger.info("Created Tenki sandbox %s (image=%s)", sandbox.id, image)

        for command in config.setup_commands:
            await self.execute_command(sandbox.id, command)

        return sandbox

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get a sandbox by ID, or None if it does not exist."""
        client = await self._get_client()
        try:
            sb = await client.get(sandbox_id)
        except Exception as exc:  # noqa: BLE001 - normalized below
            if self._is_not_found(exc):
                return None
            raise self._map_error(exc) from exc

        async with self._lock:
            self._sandboxes[sandbox_id] = sb
        return self._to_sandbox(sb, labels=dict(sb.info.metadata))

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List sandboxes, optionally filtered by labels (matched on metadata)."""
        client = await self._get_client()
        try:
            # list() pages through the whole workspace; passing workspace_id=None
            # lets the SDK fall back to the token's default workspace.
            handles = await client.list(workspace_id=self.workspace_id)
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise self._map_error(exc) from exc

        sandboxes: list[Sandbox] = []
        for sb in handles:
            metadata = dict(sb.info.metadata)
            if labels and not all(metadata.get(k) == v for k, v in labels.items()):
                continue
            async with self._lock:
                self._sandboxes[sb.id] = sb
            sandboxes.append(self._to_sandbox(sb, labels=metadata))
        return sandboxes

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy (terminate) a sandbox."""
        try:
            sb = await self._resolve(sandbox_id)
        except SandboxNotFoundError:
            return False

        # Evict before the close() yield point so concurrent _resolve callers
        # cannot observe a handle that is being terminated.
        async with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            self._destroying.add(sandbox_id)

        try:
            await sb.close_if_open()
        except Exception as exc:  # noqa: BLE001 - normalized below
            if self._is_not_found(exc):
                return False
            raise self._map_error(exc) from exc
        finally:
            async with self._lock:
                self._destroying.discard(sandbox_id)

        logger.info("Destroyed Tenki sandbox %s", sandbox_id)
        return True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a shell command in the sandbox and return its result."""
        sb = await self._resolve(sandbox_id)
        try:
            result = await sb.shell(command, timeout=timeout, env=env_vars or None)
        except Exception as exc:  # noqa: BLE001 - normalized below
            if self._is_timeout(exc):
                return ExecutionResult(
                    exit_code=124,
                    stdout="",
                    stderr=str(exc),
                    duration_ms=None,
                    timed_out=True,
                )
            raise self._map_error(exc) from exc

        return ExecutionResult(
            exit_code=result.exit_code,
            stdout=result.stdout_text,
            stderr=result.stderr_text,
            duration_ms=result.duration_ms,
            timed_out=bool(result.reason and "timeout" in result.reason.lower()),
        )

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream command stdout over the data plane, then flush any stderr."""
        sb = await self._resolve(sandbox_id)
        try:
            proc = await sb.start(
                "bash", "-lc", command, env=env_vars or None, timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise self._map_error(exc) from exc

        finished = False
        try:
            await proc.close_stdin()
            async for chunk in proc.stdout:
                if chunk:
                    yield chunk.decode(errors="replace")
            if timeout is not None:
                try:
                    result = await asyncio.wait_for(proc.wait(), timeout=timeout)
                except TimeoutError as exc:
                    raise SandboxTimeoutError(
                        f"Tenki command timed out after {timeout}s"
                    ) from exc
            else:
                result = await proc.wait()
            finished = True
            if result.stderr:
                yield f"\n[stderr]: {result.stderr_text}"
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise self._map_error(exc) from exc
        finally:
            if not finished:
                # Generator abandoned mid-stream (break/cancel/GC): don't leak
                # the remote process.
                with contextlib.suppress(Exception):
                    await proc.kill()

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------
    async def upload_file(self, sandbox_id: str, local_path: str, sandbox_path: str) -> bool:
        """Upload a local file into the sandbox."""
        validated = validate_upload_path(local_path)
        sb = await self._resolve(sandbox_id)
        try:
            await sb.fs.upload(str(validated), sandbox_path)
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise self._map_error(exc) from exc
        logger.info("Uploaded %s to %s in sandbox %s", validated, sandbox_path, sandbox_id)
        return True

    async def download_file(self, sandbox_id: str, sandbox_path: str, local_path: str) -> bool:
        """Download a file from the sandbox to the local filesystem."""
        validated = validate_download_path(local_path)
        sb = await self._resolve(sandbox_id)
        try:
            await sb.fs.download(sandbox_path, str(validated))
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise self._map_error(exc) from exc
        logger.info("Downloaded %s from sandbox %s to %s", sandbox_path, sandbox_id, validated)
        return True

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    async def health_check(self) -> bool:
        """Check whether the Tenki API is reachable and the key is valid."""
        try:
            client = await self._get_client()
            await client.who_am_i()
            return True
        except Exception:  # noqa: BLE001 - health check never raises
            return False

    async def aclose(self) -> None:
        """Close the underlying SDK client and release its channel."""
        async with self._lock:
            client = self._client
            self._client = None
            self._sandboxes.clear()
        if client is not None:
            await client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _get_client(self) -> AsyncClient:
        if self._client is None:
            try:
                from tenki import AsyncClient
            except ImportError as exc:  # pragma: no cover - import guard
                raise ProviderError(
                    'tenki SDK not installed; run pip install "cased-sandboxes[tenki]"'
                ) from exc
            self._client = AsyncClient(
                auth_token=self.api_key,
                base_url=self.base_url,
                gateway_url=self.gateway_url,
            )
        return self._client

    async def _resolve(self, sandbox_id: str) -> AsyncSandbox:
        """Return a cached AsyncSandbox handle, fetching it if not cached."""
        async with self._lock:
            if sandbox_id in self._destroying:
                raise SandboxNotFoundError(f"Tenki sandbox not found: {sandbox_id}")
            cached = self._sandboxes.get(sandbox_id)
        if cached is not None:
            return cached

        client = await self._get_client()
        try:
            sb = await client.get(sandbox_id)
        except Exception as exc:  # noqa: BLE001 - normalized below
            if self._is_not_found(exc):
                raise SandboxNotFoundError(f"Tenki sandbox not found: {sandbox_id}") from exc
            raise self._map_error(exc) from exc

        async with self._lock:
            if sandbox_id in self._destroying:
                raise SandboxNotFoundError(f"Tenki sandbox not found: {sandbox_id}")
            self._sandboxes[sandbox_id] = sb
        return sb

    def _to_sandbox(self, sb: AsyncSandbox, labels: dict[str, str]) -> Sandbox:
        """Convert an AsyncSandbox handle into a canonical Sandbox."""
        info = sb.info
        state = _STATE_MAP.get(str(info.state).upper(), SandboxState.ERROR)
        return Sandbox(
            id=info.id,
            provider=self.name,
            state=state,
            labels=dict(info.metadata) or labels,
            created_at=None,
            connection_info={
                "workspace_id": info.workspace_id,
            },
            metadata={
                "name": info.name,
                "raw_state": info.state,
                "runtime_state": info.runtime_state,
                "cpu_cores": info.cpu_cores,
                "memory_mb": info.memory_mb,
                "tags": list(info.tags),
                "sticky": info.sticky,
            },
        )

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        return type(exc).__name__ in {"SessionNotFoundError", "FileNotFoundError"}

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        return type(exc).__name__ in {"CommandTimeoutError", "PrimitiveTimeoutError"}

    @staticmethod
    def _map_error(exc: Exception) -> SandboxError:
        """Translate an SDK/transport exception into a library exception."""
        if isinstance(exc, SandboxError):
            return exc
        name = type(exc).__name__
        if name in {"UnauthorizedError", "PermissionDeniedError", "MissingAuthTokenError"}:
            return SandboxAuthenticationError(f"Tenki authentication failed: {exc}")
        if name in {"SessionNotFoundError", "FileNotFoundError"}:
            return SandboxNotFoundError(f"Tenki resource not found: {exc}")
        if name in {"CommandTimeoutError", "PrimitiveTimeoutError"}:
            return SandboxTimeoutError(f"Tenki command timed out: {exc}")
        return SandboxError(f"Tenki error ({name}): {exc}")
