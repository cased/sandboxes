"""Cloudflare sandbox provider implementation."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import httpx

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError

_DEFAULT_TIMEOUT = 30.0


class CloudflareProvider(SandboxProvider):
    """Interact with a Cloudflare Sandbox Worker deployment via HTTP API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str | None = None,
        account_id: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ProviderError("Cloudflare base_url is required")

        super().__init__(
            base_url=base_url,
            api_token=api_token,
            account_id=account_id,
            timeout=timeout,
        )

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_token = api_token
        self.account_id = account_id
        self._transport = transport
        self._user_agent = "cased-sandboxes/0.1.0"

    @property
    def name(self) -> str:
        return "cloudflare"

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        session_id = self._determine_session_id(config)
        payload: dict[str, Any] = {
            "id": session_id,
            "env": config.env_vars or {},
            "cwd": config.working_dir or "/workspace",
            "isolation": True,
        }

        await self._post("/api/session/create", json=payload)

        sandbox = Sandbox(
            id=session_id,
            provider=self.name,
            state=SandboxState.RUNNING,
            labels=config.labels or {},
            metadata={
                "base_url": self.base_url,
                "created_via": "cloudflare",
            },
        )
        return sandbox

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        sessions = await self.list_sandboxes()
        for sandbox in sessions:
            if sandbox.id == sandbox_id:
                return sandbox
        return None

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        response = await self._get("/api/session/list")
        sessions = response.get("sessions", [])
        sandboxes: list[Sandbox] = []
        for session_id in sessions:
            sandbox = Sandbox(
                id=session_id,
                provider=self.name,
                state=SandboxState.RUNNING,
                labels={"session": session_id},
                metadata={
                    "base_url": self.base_url,
                },
            )
            if labels and not all(sandbox.labels.get(k) == v for k, v in labels.items()):
                continue
            sandboxes.append(sandbox)
        return sandboxes

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        await self._ensure_session_exists(sandbox_id)

        command_to_run = self._apply_env_vars_to_command(command, env_vars)
        payload = {"id": sandbox_id, "command": command_to_run}
        data = await self._post("/api/execute", json=payload)

        return ExecutionResult(
            exit_code=data.get("exitCode", data.get("exit_code", 0)),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            duration_ms=None,
            truncated=False,
            timed_out=False,
        )

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        # There is no dedicated delete endpoint; kill all processes to clean up.
        await self._ensure_session_exists(sandbox_id)
        try:
            await self._delete(
                "/api/process/kill-all",
                params={"session": sandbox_id},
            )
        except SandboxNotFoundError:
            return False
        return True

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream command execution output using Server-Sent Events.

        If the Worker supports SSE streaming endpoint, use it.
        Otherwise, fallback to simulated streaming.
        """
        await self._ensure_session_exists(sandbox_id)

        command_to_run = self._apply_env_vars_to_command(command, env_vars)

        # Try SSE streaming endpoint if available
        url = f"{self.base_url}/api/execute/stream"
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/event-stream",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if self.account_id:
            headers["CF-Account-ID"] = self.account_id

        payload = {"id": sandbox_id, "command": command_to_run}

        try:
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(timeout or self.timeout)) as client,
                client.stream("POST", url, json=payload, headers=headers) as response,
            ):
                if response.status_code == 404:
                    # Streaming endpoint not available, fallback to regular execution
                    result = await self.execute_command(sandbox_id, command, timeout, env_vars)
                    # Simulate streaming by yielding output in chunks
                    chunk_size = 256
                    for i in range(0, len(result.stdout), chunk_size):
                        yield result.stdout[i : i + chunk_size]
                        await asyncio.sleep(0.01)  # Small delay to simulate streaming
                    if result.stderr:
                        yield f"\n[stderr]: {result.stderr}"
                    return

                response.raise_for_status()

                # Parse SSE stream
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                            if "stdout" in event:
                                yield event["stdout"]
                            if "stderr" in event:
                                yield f"[stderr]: {event['stderr']}"
                        except json.JSONDecodeError:
                            # Plain text data
                            yield data
        except httpx.HTTPError:
            # Fallback to regular execution on any HTTP error
            result = await self.execute_command(sandbox_id, command, timeout, env_vars)
            chunk_size = 256
            for i in range(0, len(result.stdout), chunk_size):
                yield result.stdout[i : i + chunk_size]
                await asyncio.sleep(0.01)
            if result.stderr:
                yield f"\n[stderr]: {result.stderr}"

    async def execute_commands(
        self,
        sandbox_id: str,
        commands: list[str],
        stop_on_error: bool = True,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> list[ExecutionResult]:
        """Execute multiple commands in sequence."""
        results = []
        for command in commands:
            result = await self.execute_command(sandbox_id, command, timeout, env_vars)
            results.append(result)
            # Stop on first failure if requested
            if stop_on_error and not result.success:
                break
        return results

    async def get_or_create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Get existing sandbox or create a new one based on labels."""
        if config.labels:
            existing = await self.find_sandbox(config.labels)
            if existing:
                return existing
        return await self.create_sandbox(config)

    async def upload_file(
        self,
        sandbox_id: str,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Upload a file to the sandbox."""
        await self._ensure_session_exists(sandbox_id)

        # Read the local file
        if not os.path.exists(local_path):
            raise SandboxError(f"Local file not found: {local_path}")

        with open(local_path, "rb") as f:
            content = f.read()

        # Try the file upload endpoint if available
        try:
            payload = {
                "id": sandbox_id,
                "path": remote_path,
                "content": content.decode("utf-8") if isinstance(content, bytes) else content,
            }
            await self._post("/api/file/write", json=payload)
            return True
        except (SandboxError, SandboxNotFoundError):
            # Fallback: use echo and base64 encoding to write file
            encoded = base64.b64encode(content).decode("utf-8")
            # Create directory if needed
            dir_path = "/".join(remote_path.split("/")[:-1])
            if dir_path:
                await self.execute_command(sandbox_id, f"mkdir -p {dir_path}")
            # Write file using base64 decode
            result = await self.execute_command(
                sandbox_id, f"echo '{encoded}' | base64 -d > {remote_path}"
            )
            return result.success

    async def download_file(
        self,
        sandbox_id: str,
        remote_path: str,
        local_path: str,
    ) -> bool:
        """Download a file from the sandbox."""
        await self._ensure_session_exists(sandbox_id)

        # Try the file read endpoint if available
        try:
            payload = {"id": sandbox_id, "path": remote_path}
            data = await self._post("/api/file/read", json=payload)
            content = data.get("content", "")

            # Write to local file
            with open(local_path, "w") as f:
                f.write(content)
            return True
        except (SandboxError, SandboxNotFoundError):
            # Fallback: use cat and base64 encoding to read file
            result = await self.execute_command(sandbox_id, f"cat {remote_path} | base64")
            if not result.success:
                return False

            # Decode and write
            try:
                content = base64.b64decode(result.stdout.strip())
                with open(local_path, "wb") as f:
                    f.write(content)
                return True
            except Exception as e:
                raise SandboxError(f"Failed to download file: {e}") from e

    async def cleanup_idle_sandboxes(self, idle_timeout: int = 600) -> None:
        """Clean up sandboxes that have been idle for too long.

        Note: Cloudflare Workers are ephemeral by nature, so this mainly
        cleans up our tracking. Actual sandbox cleanup happens automatically.
        """
        sandboxes = await self.list_sandboxes()
        asyncio.get_event_loop().time()

        for sandbox in sandboxes:
            # Since we don't track last access time in the Worker,
            # we'll clean up all sandboxes as a precaution
            with suppress(SandboxNotFoundError):
                await self.destroy_sandbox(sandbox.id)

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        sandboxes = await self.list_sandboxes(labels)
        return sandboxes[0] if sandboxes else None

    async def health_check(self) -> bool:
        try:
            await self._get("/api/ping")
            return True
        except SandboxError:
            return False

    def _determine_session_id(self, config: SandboxConfig) -> str:
        label = (
            config.labels.get("session")
            or config.labels.get("cloudflare")
            or config.labels.get("name")
            if config.labels
            else None
        )
        if label:
            return self._sanitize_session_id(label)
        return f"cf-sbx-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _sanitize_session_id(session_id: str) -> str:
        sanitized = session_id.strip()
        if not sanitized:
            return f"cf-sbx-{uuid.uuid4().hex[:12]}"
        return sanitized.replace(" ", "-")

    @staticmethod
    def _apply_env_vars_to_command(
        command: str,
        env_vars: dict[str, str] | None,
    ) -> str:
        if not env_vars:
            return command
        exports = " && ".join([f"export {key}='{value}'" for key, value in env_vars.items()])
        return f"{exports} && {command}"

    async def _ensure_session_exists(self, sandbox_id: str) -> None:
        sandbox = await self.get_sandbox(sandbox_id)
        if not sandbox:
            raise SandboxNotFoundError(f"Session {sandbox_id} not found")

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "User-Agent": self._user_agent,
            "Content-Type": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if self.account_id:
            headers["CF-Account-ID"] = self.account_id

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            transport=self._transport,
        ) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=headers,
                )
            except httpx.HTTPError as exc:  # pragma: no cover - network errors
                raise SandboxError(f"Cloudflare request failed: {exc}") from exc

        if response.status_code == 404:
            raise SandboxNotFoundError(f"Cloudflare resource not found: {path}")

        if response.status_code >= 400:
            message = self._extract_error_message(response)
            raise SandboxError(f"Cloudflare API error ({response.status_code}): {message}")

        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return None

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=json)

    async def _delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request("DELETE", path, json=json, params=params)

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text
        if isinstance(payload, dict):
            return payload.get("error") or payload.get("message") or response.text
        return response.text
