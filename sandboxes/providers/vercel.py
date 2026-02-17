"""Vercel sandbox provider using the official vercel SDK."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime
from typing import Any

from ..base import (
    ExecutionResult,
    ProviderCapabilities,
    Sandbox,
    SandboxConfig,
    SandboxProvider,
    SandboxState,
)
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError
from ..security import validate_download_path, validate_upload_path

logger = logging.getLogger(__name__)

try:
    from vercel.oidc import get_credentials as get_vercel_credentials
    from vercel.sandbox import AsyncSandbox as VercelSandbox
    from vercel.sandbox.api_client import AsyncAPIClient
    from vercel.sandbox.base_client import APIError as VercelAPIError
    from vercel.sandbox.models import SandboxesResponse

    VERCEL_AVAILABLE = True
except ImportError:
    VERCEL_AVAILABLE = False
    VercelSandbox = None
    AsyncAPIClient = None
    VercelAPIError = Exception
    SandboxesResponse = None
    get_vercel_credentials = None
    logger.warning("Vercel SDK not available - install with: pip install vercel")


class VercelProvider(SandboxProvider):
    """Vercel sandbox provider implementation."""

    CAPABILITIES = ProviderCapabilities(
        persistent=True,
        snapshot=True,
        streaming=True,
        file_upload=True,
        interactive_shell=True,
    )

    def __init__(
        self,
        token: str | None = None,
        project_id: str | None = None,
        team_id: str | None = None,
        **config,
    ):
        """Initialize Vercel provider."""
        super().__init__(**config)

        if not VERCEL_AVAILABLE:
            raise ProviderError("Vercel SDK not installed")

        provided_token = (
            token
            or os.getenv("VERCEL_TOKEN")
            or os.getenv("VERCEL_API_TOKEN")
            or os.getenv("VERCEL_ACCESS_TOKEN")
        )
        provided_project_id = project_id or os.getenv("VERCEL_PROJECT_ID")
        provided_team_id = team_id or os.getenv("VERCEL_TEAM_ID")

        try:
            credentials = get_vercel_credentials(
                token=provided_token,
                project_id=provided_project_id,
                team_id=provided_team_id,
            )
        except RuntimeError as e:
            raise ProviderError(
                "Vercel credentials not provided. Set VERCEL_TOKEN, "
                "VERCEL_PROJECT_ID, and VERCEL_TEAM_ID."
            ) from e

        self.token = credentials.token
        self.project_id = credentials.project_id
        self.team_id = credentials.team_id
        self.default_timeout_seconds = int(config.get("timeout", 300))
        self.default_runtime = config.get("runtime")
        self.default_ports = list(config.get("ports", []))
        self.default_interactive = bool(config.get("interactive", False))

        self._sandboxes: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Provider name."""
        return "vercel"

    def _auth_kwargs(self) -> dict[str, str]:
        return {
            "token": self.token,
            "project_id": self.project_id,
            "team_id": self.team_id,
        }

    @staticmethod
    def _convert_state(vercel_state: str) -> SandboxState:
        state_map = {
            "pending": SandboxState.CREATING,
            "running": SandboxState.RUNNING,
            "stopping": SandboxState.STOPPING,
            "stopped": SandboxState.STOPPED,
            "snapshotting": SandboxState.STOPPING,
            "failed": SandboxState.ERROR,
        }
        return state_map.get(vercel_state.lower(), SandboxState.ERROR)

    @staticmethod
    def _to_datetime(timestamp_ms: int | None) -> datetime | None:
        if timestamp_ms is None:
            return None
        return datetime.fromtimestamp(timestamp_ms / 1000)

    def _to_sandbox(self, vercel_sandbox, metadata: dict[str, Any]) -> Sandbox:
        raw = vercel_sandbox.sandbox
        routes = metadata.get("routes") or vercel_sandbox.routes

        return Sandbox(
            id=vercel_sandbox.sandbox_id,
            provider=self.name,
            state=self._convert_state(raw.status),
            labels=metadata.get("labels", {}),
            created_at=metadata.get("created_at") or self._to_datetime(raw.created_at),
            connection_info={"routes": routes},
            metadata={
                "status_raw": raw.status,
                "runtime": raw.runtime,
                "region": raw.region,
                "timeout_ms": raw.timeout,
                "memory_mb": raw.memory,
                "vcpus": raw.vcpus,
                "interactive_port": raw.interactive_port,
                "last_accessed": metadata.get("last_accessed", time.time()),
            },
        )

    def _build_resources(self, config: SandboxConfig) -> dict[str, Any] | None:
        resources = {}
        if config.provider_config:
            resources.update(config.provider_config.get("resources", {}))

        if config.memory_mb and "memory" not in resources:
            resources["memory"] = config.memory_mb

        if config.cpu_cores and "vcpus" not in resources:
            resources["vcpus"] = max(1, math.ceil(config.cpu_cores))

        return resources or None

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        if isinstance(error, VercelAPIError) and getattr(error, "status_code", None) == 404:
            return True
        message = str(error).lower()
        return "404" in message or "not found" in message

    async def _get_or_fetch_sdk_sandbox(self, sandbox_id: str):
        metadata = self._sandboxes.get(sandbox_id, {})
        sdk_sandbox = metadata.get("vercel_sandbox")
        if sdk_sandbox is not None:
            return sdk_sandbox, metadata

        try:
            sdk_sandbox = await VercelSandbox.get(sandbox_id=sandbox_id, **self._auth_kwargs())
        except Exception as e:
            if self._is_not_found(e):
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found") from e
            raise SandboxError(f"Failed to fetch sandbox {sandbox_id}: {e}") from e

        metadata.update(
            {
                "vercel_sandbox": sdk_sandbox,
                "routes": sdk_sandbox.routes,
                "created_at": metadata.get("created_at")
                or self._to_datetime(sdk_sandbox.sandbox.created_at),
                "last_accessed": time.time(),
                "labels": metadata.get("labels", {}),
                "env_vars": metadata.get("env_vars", {}),
                "working_dir": metadata.get("working_dir"),
            }
        )

        async with self._lock:
            self._sandboxes[sandbox_id] = metadata

        return sdk_sandbox, metadata

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new Vercel sandbox."""
        try:
            timeout_seconds = config.timeout_seconds or self.default_timeout_seconds
            timeout_ms = max(1000, int(timeout_seconds * 1000))
            provider_config = config.provider_config or {}

            runtime = provider_config.get("runtime") or config.image or self.default_runtime
            ports = provider_config.get("ports", self.default_ports)
            source = provider_config.get("source")
            interactive = provider_config.get("interactive", self.default_interactive)
            resources = self._build_resources(config)

            vercel_sandbox = await VercelSandbox.create(
                source=source,
                ports=ports,
                timeout=timeout_ms,
                resources=resources,
                runtime=runtime,
                interactive=interactive,
                **self._auth_kwargs(),
            )

            metadata = {
                "vercel_sandbox": vercel_sandbox,
                "labels": config.labels or {},
                "created_at": self._to_datetime(vercel_sandbox.sandbox.created_at),
                "last_accessed": time.time(),
                "routes": vercel_sandbox.routes,
                "env_vars": dict(config.env_vars or {}),
                "working_dir": config.working_dir,
                "config": config,
            }

            async with self._lock:
                self._sandboxes[vercel_sandbox.sandbox_id] = metadata

            logger.info("Created Vercel sandbox %s", vercel_sandbox.sandbox_id)

            if config.setup_commands:
                for cmd in config.setup_commands:
                    await self.execute_command(vercel_sandbox.sandbox_id, cmd)

            return self._to_sandbox(vercel_sandbox, metadata)

        except Exception as e:
            raise SandboxError(f"Failed to create Vercel sandbox: {e}") from e

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID."""
        try:
            vercel_sandbox, metadata = await self._get_or_fetch_sdk_sandbox(sandbox_id)
            metadata["last_accessed"] = time.time()
            return self._to_sandbox(vercel_sandbox, metadata)
        except SandboxNotFoundError:
            return None
        except Exception as e:
            raise SandboxError(f"Failed to get sandbox {sandbox_id}: {e}") from e

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List Vercel sandboxes for this project."""
        client = None
        listed_sandboxes: list[Sandbox] = []

        try:
            client = AsyncAPIClient(team_id=self.team_id, token=self.token)
            response_data = await client.request_json(
                "GET",
                "/v1/sandboxes",
                query={"project": self.project_id, "limit": 100},
            )
            parsed = SandboxesResponse.model_validate(response_data)

            for listed in parsed.sandboxes:
                metadata = self._sandboxes.get(listed.id, {})
                metadata.setdefault("labels", {})
                metadata.setdefault("env_vars", {})
                metadata["created_at"] = metadata.get("created_at") or self._to_datetime(
                    listed.created_at
                )
                metadata["last_accessed"] = metadata.get("last_accessed", time.time())
                metadata.setdefault("routes", [])

                async with self._lock:
                    self._sandboxes[listed.id] = metadata

                if labels and not all(metadata["labels"].get(k) == v for k, v in labels.items()):
                    continue

                listed_sandboxes.append(
                    Sandbox(
                        id=listed.id,
                        provider=self.name,
                        state=self._convert_state(listed.status),
                        labels=metadata["labels"],
                        created_at=metadata["created_at"],
                        connection_info={"routes": metadata.get("routes", [])},
                        metadata={
                            "status_raw": listed.status,
                            "runtime": listed.runtime,
                            "region": listed.region,
                            "timeout_ms": listed.timeout,
                            "memory_mb": listed.memory,
                            "vcpus": listed.vcpus,
                            "interactive_port": listed.interactive_port,
                            "last_accessed": metadata["last_accessed"],
                        },
                    )
                )

            return listed_sandboxes

        except Exception as e:
            logger.warning("Could not list Vercel sandboxes from API: %s", e)

            # Fallback to locally tracked sandboxes.
            local_sandboxes = []
            for sandbox_id in list(self._sandboxes.keys()):
                sandbox = await self.get_sandbox(sandbox_id)
                if not sandbox:
                    continue
                if labels and not all(sandbox.labels.get(k) == v for k, v in labels.items()):
                    continue
                local_sandboxes.append(sandbox)
            return local_sandboxes

        finally:
            if client is not None:
                with suppress(Exception):
                    await client.aclose()

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute command in sandbox via sh -lc."""
        try:
            vercel_sandbox, metadata = await self._get_or_fetch_sdk_sandbox(sandbox_id)

            merged_env = dict(metadata.get("env_vars", {}))
            if env_vars:
                merged_env.update(env_vars)

            working_dir = metadata.get("working_dir")
            start = time.time()

            detached_cmd = await vercel_sandbox.run_command_detached(
                "sh",
                ["-lc", command],
                cwd=working_dir,
                env=merged_env or None,
            )

            try:
                finished_cmd = (
                    await asyncio.wait_for(detached_cmd.wait(), timeout=timeout)
                    if timeout
                    else await detached_cmd.wait()
                )
            except TimeoutError:
                with suppress(Exception):
                    await detached_cmd.kill()
                stdout, stderr = await asyncio.gather(detached_cmd.stdout(), detached_cmd.stderr())
                duration_ms = int((time.time() - start) * 1000)
                return ExecutionResult(
                    exit_code=-1,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=duration_ms,
                    truncated=False,
                    timed_out=True,
                )

            stdout, stderr = await asyncio.gather(finished_cmd.stdout(), finished_cmd.stderr())
            duration_ms = int((time.time() - start) * 1000)
            metadata["last_accessed"] = time.time()

            return ExecutionResult(
                exit_code=finished_cmd.exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                truncated=False,
                timed_out=False,
            )

        except SandboxNotFoundError:
            raise
        except Exception as e:
            if self._is_not_found(e):
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found") from e
            raise SandboxError(f"Failed to execute command in sandbox {sandbox_id}: {e}") from e

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream command output from Vercel logs endpoint."""
        try:
            vercel_sandbox, metadata = await self._get_or_fetch_sdk_sandbox(sandbox_id)

            merged_env = dict(metadata.get("env_vars", {}))
            if env_vars:
                merged_env.update(env_vars)

            working_dir = metadata.get("working_dir")
            detached_cmd = await vercel_sandbox.run_command_detached(
                "sh",
                ["-lc", command],
                cwd=working_dir,
                env=merged_env or None,
            )

            async for log_line in detached_cmd.logs():
                if log_line.stream == "stderr":
                    yield f"[stderr]: {log_line.data}"
                else:
                    yield log_line.data

            if timeout:
                await asyncio.wait_for(detached_cmd.wait(), timeout=timeout)
            else:
                await detached_cmd.wait()
            metadata["last_accessed"] = time.time()

        except SandboxNotFoundError:
            raise
        except Exception as e:
            if self._is_not_found(e):
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found") from e
            raise SandboxError(
                f"Failed to stream command output in sandbox {sandbox_id}: {e}"
            ) from e

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Stop and remove sandbox."""
        try:
            vercel_sandbox, _metadata = await self._get_or_fetch_sdk_sandbox(sandbox_id)
        except SandboxNotFoundError:
            return False

        try:
            await vercel_sandbox.stop()
            with suppress(Exception):
                await vercel_sandbox.client.aclose()
            self._sandboxes.pop(sandbox_id, None)
            return True
        except Exception as e:
            if self._is_not_found(e):
                self._sandboxes.pop(sandbox_id, None)
                return False
            raise SandboxError(f"Failed to destroy sandbox {sandbox_id}: {e}") from e

    async def upload_file(self, sandbox_id: str, local_path: str, sandbox_path: str) -> bool:
        """Upload local file into sandbox filesystem."""
        try:
            validated_path = validate_upload_path(local_path)
            vercel_sandbox, _metadata = await self._get_or_fetch_sdk_sandbox(sandbox_id)

            with open(validated_path, "rb") as f:
                content = f.read()

            await vercel_sandbox.write_files([{"path": sandbox_path, "content": content}])
            return True

        except SandboxNotFoundError:
            raise
        except Exception as e:
            raise SandboxError(f"Failed to upload file to sandbox {sandbox_id}: {e}") from e

    async def download_file(self, sandbox_id: str, sandbox_path: str, local_path: str) -> bool:
        """Download file from sandbox filesystem."""
        try:
            validated_path = validate_download_path(local_path)
            vercel_sandbox, _metadata = await self._get_or_fetch_sdk_sandbox(sandbox_id)

            content = await vercel_sandbox.read_file(sandbox_path)
            if content is None:
                raise SandboxNotFoundError(f"Sandbox file not found: {sandbox_path}")

            with open(validated_path, "wb") as f:
                f.write(content)
            return True

        except SandboxNotFoundError:
            raise
        except Exception as e:
            raise SandboxError(f"Failed to download file from sandbox {sandbox_id}: {e}") from e
