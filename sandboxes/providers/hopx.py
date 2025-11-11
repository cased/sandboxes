"""Hopx sandbox provider implementation."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import httpx

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_BASE_URL = "https://api.hopx.dev"
_DEFAULT_TEMPLATE = "code-interpreter"  # Default template for sandbox creation


class HopxProvider(SandboxProvider):
    """Interact with Hopx sandboxes via their HTTP API.

    Hopx provides a two-tier API:
    - Control Plane (api.hopx.dev): Sandbox lifecycle management
    - Data Plane ({sandbox_id}.hopx.dev): Code execution and file operations

    Features:
    - Template-based sandbox creation with sub-100ms boot times
    - Multiple sandbox states: creating, running, stopped, paused
    - Rich output support for plots and DataFrames
    - WebSocket streaming for real-time execution
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        default_template: str = _DEFAULT_TEMPLATE,
        timeout: float = _DEFAULT_TIMEOUT,
        **config: Any,
    ) -> None:
        """Initialize the Hopx provider.

        Args:
            api_key: Hopx API key (format: hopx_live_<keyId>.<secret>).
                    Falls back to HOPX_API_KEY environment variable.
            base_url: Base URL for the Hopx API (default: https://api.hopx.dev)
            default_template: Default template to use for sandbox creation
            timeout: Request timeout in seconds
            **config: Additional configuration options

        Raises:
            ProviderError: If API key is not provided and not found in environment
        """
        super().__init__(**config)

        self.api_key = api_key or os.getenv("HOPX_API_KEY")
        if not self.api_key:
            raise ProviderError(
                "Hopx API key not provided. Set HOPX_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self.base_url = base_url.rstrip("/")
        self.default_template = default_template
        self.timeout = timeout
        self._user_agent = "sandboxes/0.2.3"

        # Track sandboxes locally for metadata management
        self._sandboxes: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Provider name identifier."""
        return "hopx"

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new Hopx sandbox from a template.

        Args:
            config: Sandbox configuration including template, labels, and environment

        Returns:
            Sandbox: Created sandbox instance

        Raises:
            SandboxError: If sandbox creation fails
        """
        # Determine template from config or use default
        template = (
            config.provider_config.get("template")
            if config.provider_config
            else None
        ) or self.default_template

        # Prepare creation payload
        payload: dict[str, Any] = {
            "template_name": template,
        }

        # Add environment variables if provided
        if config.env_vars:
            payload["env_vars"] = config.env_vars

        # Create sandbox via control plane API
        response = await self._post("/v1/sandboxes", json=payload)
        sandbox_id = response.get("id")
        public_host = response.get("public_host") or response.get("direct_url")  # Data plane URL
        auth_token = response.get("auth_token")  # JWT for data plane authentication

        if not sandbox_id:
            raise SandboxError("Failed to create sandbox: No ID returned")

        # Wait for sandbox to transition from 'creating' to 'running'
        await self._wait_for_sandbox_ready(sandbox_id)

        # Store metadata locally
        async with self._lock:
            self._sandboxes[sandbox_id] = {
                "labels": config.labels or {},
                "created_at": time.time(),
                "last_accessed": time.time(),
                "template": template,
                "public_host": public_host,  # Store for data plane operations
                "auth_token": auth_token,  # JWT for data plane authentication
            }

        # Convert to standard Sandbox object
        return await self._to_sandbox(sandbox_id, response)

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Retrieve sandbox details by ID.

        Args:
            sandbox_id: Unique sandbox identifier

        Returns:
            Sandbox object if found, None otherwise
        """
        try:
            response = await self._get(f"/v1/sandboxes/{sandbox_id}")

            # Update last accessed time
            async with self._lock:
                if sandbox_id in self._sandboxes:
                    self._sandboxes[sandbox_id]["last_accessed"] = time.time()

            return await self._to_sandbox(sandbox_id, response)
        except SandboxNotFoundError:
            return None

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List all sandboxes, optionally filtered by labels.

        Args:
            labels: Optional label filters (applied locally)

        Returns:
            List of Sandbox objects
        """
        response = await self._get("/v1/sandboxes")
        sandboxes_data = response.get("sandboxes", [])

        sandboxes: list[Sandbox] = []
        for sandbox_data in sandboxes_data:
            sandbox_id = sandbox_data.get("id")
            if not sandbox_id:
                continue

            sandbox = await self._to_sandbox(sandbox_id, sandbox_data)

            # Apply label filtering
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
        """Execute a shell command in the sandbox.

        Args:
            sandbox_id: Sandbox identifier
            command: Shell command to execute
            timeout: Optional timeout in seconds
            env_vars: Optional environment variables for the command

        Returns:
            ExecutionResult with stdout, stderr, and exit code

        Raises:
            SandboxNotFoundError: If sandbox doesn't exist
            SandboxError: If execution fails
        """
        # Get public_host and auth_token for data plane operations
        async with self._lock:
            if sandbox_id in self._sandboxes:
                self._sandboxes[sandbox_id]["last_accessed"] = time.time()
                public_host = self._sandboxes[sandbox_id].get("public_host")
                auth_token = self._sandboxes[sandbox_id].get("auth_token")
            else:
                public_host = None
                auth_token = None

        # If we don't have public_host cached, try to get it from API
        if not public_host or not auth_token:
            sandbox_info = await self.get_sandbox(sandbox_id)
            if sandbox_info:
                public_host = sandbox_info.metadata.get("public_host") or sandbox_info.metadata.get("direct_url")
                auth_token = sandbox_info.metadata.get("auth_token")
                # Cache for future use
                async with self._lock:
                    if sandbox_id in self._sandboxes:
                        self._sandboxes[sandbox_id]["public_host"] = public_host
                        self._sandboxes[sandbox_id]["auth_token"] = auth_token

        if not public_host:
            raise SandboxError(f"No public_host available for sandbox {sandbox_id}")

        if not auth_token:
            raise SandboxError(f"No auth_token available for sandbox {sandbox_id}")

        # Apply environment variables to command if provided
        command_to_run = self._apply_env_vars_to_command(command, env_vars)

        # Execute via data plane API
        payload = {
            "command": command_to_run,
        }

        if timeout:
            payload["timeout"] = timeout

        # Use data plane endpoint from public_host with JWT auth
        response = await self._post_to_data_plane(
            public_host,
            "/commands/run",
            json=payload,
            auth_token=auth_token,
        )

        # Parse execution result
        return ExecutionResult(
            exit_code=response.get("exitCode", 0),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
            duration_ms=response.get("duration"),
            truncated=False,
            timed_out=response.get("timedOut", False),
        )

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox and clean up resources.

        Args:
            sandbox_id: Sandbox identifier

        Returns:
            True if deletion was successful, False otherwise
        """
        try:
            await self._delete(f"/v1/sandboxes/{sandbox_id}")

            # Remove from local tracking
            async with self._lock:
                self._sandboxes.pop(sandbox_id, None)

            return True
        except SandboxNotFoundError:
            return False

    async def upload_file(
        self,
        sandbox_id: str,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Upload a file to the sandbox.

        Args:
            sandbox_id: Sandbox identifier
            local_path: Local file path to upload
            remote_path: Destination path in sandbox

        Returns:
            True if upload successful

        Raises:
            SandboxError: If file doesn't exist or upload fails
        """
        if not os.path.exists(local_path):
            raise SandboxError(f"Local file not found: {local_path}")

        # Read file content
        with open(local_path, "rb") as f:
            content = f.read()

        # Encode as base64 for JSON transport
        encoded_content = base64.b64encode(content).decode("utf-8")

        # Upload via data plane file write endpoint
        data_plane_url = f"https://{sandbox_id}.hopx.dev"
        payload = {
            "path": remote_path,
            "content": encoded_content,
            "encoding": "base64",
        }

        await self._post_to_data_plane(data_plane_url, "/files/write", json=payload)
        return True

    async def download_file(
        self,
        sandbox_id: str,
        remote_path: str,
        local_path: str,
    ) -> bool:
        """Download a file from the sandbox.

        Args:
            sandbox_id: Sandbox identifier
            remote_path: Source file path in sandbox
            local_path: Local destination path

        Returns:
            True if download successful

        Raises:
            SandboxError: If download fails
        """
        # Download via data plane file read endpoint
        data_plane_url = f"https://{sandbox_id}.hopx.dev"
        params = {"path": remote_path}

        response = await self._get_from_data_plane(
            data_plane_url,
            "/files/read",
            params=params,
        )

        content = response.get("content", "")
        encoding = response.get("encoding", "utf-8")

        # Decode content based on encoding
        if encoding == "base64":
            decoded_content = base64.b64decode(content)
            with open(local_path, "wb") as f:
                f.write(decoded_content)
        else:
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(content)

        return True

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream command execution output in real-time.

        Uses WebSocket connection to the data plane for streaming.
        Falls back to regular execution if streaming is not available.

        Args:
            sandbox_id: Sandbox identifier
            command: Command to execute
            timeout: Optional timeout in seconds
            env_vars: Optional environment variables

        Yields:
            Output chunks as they become available
        """
        # Update last accessed time
        async with self._lock:
            if sandbox_id in self._sandboxes:
                self._sandboxes[sandbox_id]["last_accessed"] = time.time()

        # For now, fall back to simulated streaming from regular execution
        # TODO: Implement WebSocket streaming when needed
        result = await self.execute_command(sandbox_id, command, timeout, env_vars)

        # Simulate streaming by yielding in chunks
        chunk_size = 256
        for i in range(0, len(result.stdout), chunk_size):
            yield result.stdout[i : i + chunk_size]
            await asyncio.sleep(0.01)  # Small delay to simulate streaming

        if result.stderr:
            yield f"\n[stderr]: {result.stderr}"

    async def health_check(self) -> bool:
        """Check if the Hopx API is accessible.

        Returns:
            True if API is healthy, False otherwise
        """
        try:
            # List sandboxes as a health check
            await self._get("/v1/sandboxes")
            return True
        except SandboxError:
            return False

    async def cleanup_idle_sandboxes(self, idle_timeout: int = 600) -> None:
        """Clean up sandboxes that have been idle for too long.

        Args:
            idle_timeout: Idle time threshold in seconds (default: 10 minutes)
        """
        current_time = time.time()
        sandboxes_to_cleanup: list[str] = []

        async with self._lock:
            for sandbox_id, metadata in self._sandboxes.items():
                last_accessed = metadata.get("last_accessed", 0)
                if current_time - last_accessed > idle_timeout:
                    sandboxes_to_cleanup.append(sandbox_id)

        # Clean up idle sandboxes
        for sandbox_id in sandboxes_to_cleanup:
            with suppress(SandboxNotFoundError):
                await self.destroy_sandbox(sandbox_id)

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find a sandbox matching the given labels.

        Args:
            labels: Labels to match

        Returns:
            First matching sandbox or None
        """
        sandboxes = await self.list_sandboxes(labels)

        # Return the most recently accessed sandbox if multiple matches
        if sandboxes:
            async with self._lock:
                sandboxes.sort(
                    key=lambda s: self._sandboxes.get(s.id, {}).get("last_accessed", 0),
                    reverse=True,
                )
            return sandboxes[0]

        return None

    async def _wait_for_sandbox_ready(
        self,
        sandbox_id: str,
        max_wait: int = 300,  # 5 minutes for template-based sandboxes
        poll_interval: float = 2.0,
    ) -> None:
        """Wait for sandbox to transition to 'running' state.

        Args:
            sandbox_id: Sandbox identifier
            max_wait: Maximum wait time in seconds
            poll_interval: Polling interval in seconds

        Raises:
            SandboxError: If sandbox doesn't become ready in time
        """
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                response = await self._get(f"/v1/sandboxes/{sandbox_id}")
                # API uses "status" field, not "state"
                status = response.get("status", "").lower()

                if status == "running":
                    return

                if status in ("stopped", "paused", "deleted"):
                    raise SandboxError(
                        f"Sandbox {sandbox_id} is in unexpected status: {status}"
                    )

                # Continue waiting if still creating
                await asyncio.sleep(poll_interval)

            except SandboxNotFoundError:
                raise SandboxError(f"Sandbox {sandbox_id} not found during creation") from None

        raise SandboxError(
            f"Sandbox {sandbox_id} did not become ready within {max_wait} seconds"
        )

    async def _to_sandbox(self, sandbox_id: str, api_data: dict[str, Any]) -> Sandbox:
        """Convert API response to Sandbox object.

        Args:
            sandbox_id: Sandbox identifier
            api_data: Raw API response data

        Returns:
            Sandbox object
        """
        # Map Hopx status to SandboxState enum (API uses "status" field)
        status_str = api_data.get("status", api_data.get("state", "running")).lower()
        status_map = {
            "running": SandboxState.RUNNING,
            "stopped": SandboxState.STOPPED,
            "paused": SandboxState.STOPPED,
            "creating": SandboxState.RUNNING,  # Treat as running for simplicity
            "deleted": SandboxState.STOPPED,
        }
        state = status_map.get(status_str, SandboxState.RUNNING)

        # Get local metadata
        async with self._lock:
            local_metadata = self._sandboxes.get(sandbox_id, {})

        return Sandbox(
            id=sandbox_id,
            provider=self.name,
            state=state,
            labels=local_metadata.get("labels", {}),
            created_at=local_metadata.get("created_at"),
            metadata={
                "template": local_metadata.get("template") or api_data.get("template_id") or api_data.get("template_name"),
                "public_host": api_data.get("public_host") or api_data.get("direct_url") or local_metadata.get("public_host"),
                "api_status": status_str,
                **api_data,
            },
        )

    @staticmethod
    def _apply_env_vars_to_command(
        command: str,
        env_vars: dict[str, str] | None,
    ) -> str:
        """Apply environment variables to a command.

        Args:
            command: Base command
            env_vars: Environment variables to export

        Returns:
            Command with environment variable exports prepended
        """
        if not env_vars:
            return command
        exports = " && ".join([f"export {k}='{v}'" for k, v in env_vars.items()])
        return f"{exports} && {command}"

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        base_url: str | None = None,
        auth_token: str | None = None,
    ) -> Any:
        """Make an HTTP request to the Hopx API.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            path: API endpoint path
            json: Optional JSON payload
            params: Optional query parameters
            base_url: Optional override for base URL (for data plane requests)

        Returns:
            Parsed JSON response

        Raises:
            SandboxNotFoundError: If resource not found (404)
            SandboxError: For other API errors
        """
        url = f"{base_url or self.base_url}{path}"
        headers = {
            "User-Agent": self._user_agent,
            "Content-Type": "application/json",
        }

        # Control plane uses X-API-Key, data plane uses Bearer token
        if auth_token:
            # Data plane authentication with JWT
            headers["Authorization"] = f"Bearer {auth_token}"
        elif self.api_key:
            # Control plane authentication with API key
            headers["X-API-Key"] = self.api_key

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise SandboxError(f"Hopx request failed: {exc}") from exc

        # Handle 404 errors
        if response.status_code == 404:
            raise SandboxNotFoundError(f"Hopx resource not found: {path}")

        # Handle other errors
        if response.status_code >= 400:
            message = self._extract_error_message(response)
            raise SandboxError(f"Hopx API error ({response.status_code}): {message}")

        # Parse JSON response
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()

        return None

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to the control plane API."""
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        """Make a POST request to the control plane API."""
        return await self._request("POST", path, json=json)

    async def _delete(self, path: str) -> Any:
        """Make a DELETE request to the control plane API."""
        return await self._request("DELETE", path)

    async def _get_from_data_plane(
        self,
        data_plane_url: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> Any:
        """Make a GET request to the data plane API."""
        return await self._request("GET", path, params=params, base_url=data_plane_url, auth_token=auth_token)

    async def _post_to_data_plane(
        self,
        data_plane_url: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        auth_token: str | None = None,
    ) -> Any:
        """Make a POST request to the data plane API."""
        return await self._request("POST", path, json=json, base_url=data_plane_url, auth_token=auth_token)

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        """Extract error message from API response.

        Args:
            response: HTTP response object

        Returns:
            Error message string
        """
        try:
            payload = response.json()
        except ValueError:
            return response.text

        if isinstance(payload, dict):
            return (
                payload.get("error")
                or payload.get("message")
                or payload.get("detail")
                or response.text
            )

        return response.text
