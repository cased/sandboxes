"""Local and Cloud microVM sandboxes backed by the Smol Machines SDK."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from pathlib import Path

from smol import ConnectOptions, ExecOptions, Machine, MachineConfig, ResourceSpec

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

_MARKER = "cased-sandboxes"


class SmolProvider(SandboxProvider):
    """Run sandboxes on local hardware or Smol Cloud (selected explicitly)."""

    CAPABILITIES = ProviderCapabilities(persistent=True, file_upload=True)

    def __init__(
        self,
        target: str = "local",
        api_key: str | None = None,
        base_url: str | None = None,
        image: str = "python:3.12-alpine",
        ttl_seconds: int = 3600,
        **config,
    ):
        super().__init__(**config)
        if target not in ("local", "cloud"):
            raise ProviderError("Smol target must be 'local' or 'cloud'")
        if ttl_seconds <= 0:
            raise ProviderError("Smol Cloud TTL must be positive")
        self.target = target
        self.image = image
        self.ttl_seconds = ttl_seconds
        self._connection = ConnectOptions(target=target, api_key=api_key, base_url=base_url)

    @property
    def name(self) -> str:
        return "smol"

    def _summary(self, machine) -> Sandbox:
        states = {
            "created": SandboxState.CREATING,
            "starting": SandboxState.STARTING,
            "running": SandboxState.RUNNING,
            "started": SandboxState.RUNNING,
            "stopping": SandboxState.STOPPING,
            "stopped": SandboxState.STOPPED,
            "paused": SandboxState.STOPPED,
            "terminated": SandboxState.TERMINATED,
            "error": SandboxState.ERROR,
            "failed": SandboxState.ERROR,
        }
        created = getattr(machine, "created_at", None)
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                created = None
        return Sandbox(
            id=machine.id,
            provider=self.name,
            state=states.get(machine.state.lower(), SandboxState.ERROR),
            labels={key: value for key, value in machine.labels.items() if key != _MARKER},
            created_at=created,
            metadata={"image": machine.image, "target": self.target},
        )

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a persistent machine and remove it if setup fails."""
        options = config.provider_config or {}
        network = options.get("network", True)
        hosts = options.get("allow_hosts")
        cidrs = options.get("allow_cidrs")
        if not network and (hosts or cidrs):
            raise ProviderError("Network allow lists require network=True")
        if config.cpu_cores is not None and config.cpu_cores <= 0:
            raise ProviderError("Smol CPU count must be positive")
        if config.memory_mb is not None and config.memory_mb <= 0:
            raise ProviderError("Smol memory must be positive")
        resources = ResourceSpec(
            cpus=math.ceil(config.cpu_cores) if config.cpu_cores is not None else None,
            memory_mb=config.memory_mb,
            network=network,
            allow_hosts=hosts,
            allow_cidrs=cidrs,
        )
        machine = await asyncio.to_thread(
            Machine.create,
            MachineConfig(
                image=config.image or self.image,
                labels={**(config.labels or {}), _MARKER: "true"},
                env=config.env_vars or None,
                resources=resources,
                workdir=config.working_dir,
                persistent=True,
                ttl_seconds=self.ttl_seconds if self.target == "cloud" else None,
            ),
            self._connection,
        )
        try:
            for command in config.setup_commands:
                result = await self.execute_command(machine.id, command, config.timeout_seconds)
                if not result.success:
                    raise SandboxError(
                        f"Smol setup command failed (exit {result.exit_code}): {result.stderr}"
                    )
            summary = await self.get_sandbox(machine.id)
            if summary is None:
                raise SandboxError("Created Smol machine was not visible in the sandbox list")
            return summary
        except BaseException:
            await asyncio.to_thread(machine.delete)
            raise

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """Only list machines created through this provider."""
        if labels and labels.get(_MARKER, "true") != "true":
            return []
        required = {**(labels or {}), _MARKER: "true"}
        machines = await asyncio.to_thread(Machine.list, self._connection, required)
        return [
            self._summary(machine) for machine in machines if machine.labels.get(_MARKER) == "true"
        ]

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        machines = await self.list_sandboxes()
        return next((machine for machine in machines if machine.id == sandbox_id), None)

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Reuse a matching stopped sandbox instead of losing its persisted work."""
        sandboxes = await self.list_sandboxes(labels=labels)
        for sandbox in sandboxes:
            if sandbox.state == SandboxState.RUNNING:
                return sandbox
        for sandbox in sandboxes:
            if sandbox.state == SandboxState.STOPPED:
                await self._owned_machine(sandbox.id)
                return await self.get_sandbox(sandbox.id)
        return None

    async def _owned_machine(self, sandbox_id: str) -> Machine:
        sandbox = await self.get_sandbox(sandbox_id)
        if sandbox is None:
            raise SandboxNotFoundError(f"Smol sandbox {sandbox_id} not found")
        machine = await asyncio.to_thread(Machine.connect, sandbox_id, self._connection)
        # Local connect starts a stopped machine; Cloud connect only attaches.
        # Reuse a stopped sandbox without discarding a paused machine's RAM.
        if self.target == "cloud" and sandbox.state == SandboxState.STOPPED:
            state = await asyncio.to_thread(machine.state)
            if state == "paused":
                await asyncio.to_thread(machine.resume)
            elif state == "stopped":
                await asyncio.to_thread(machine.start)
        return machine

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        machine = await self._owned_machine(sandbox_id)
        result = await asyncio.to_thread(
            machine.exec,
            ["sh", "-lc", command],
            ExecOptions(timeout=timeout, env=env_vars),
        )
        return ExecutionResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            truncated=result.stdout_truncated or result.stderr_truncated,
        )

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        if await self.get_sandbox(sandbox_id) is None:
            return False
        machine = await asyncio.to_thread(Machine.connect, sandbox_id, self._connection)
        try:
            await asyncio.to_thread(machine.delete)
        except Exception as exc:
            if getattr(exc, "code", None) != "NOT_FOUND":
                raise
        return True

    def _validate_cloud_file_path(self, path: str) -> None:
        # Cloud's file route can reinterpret URL delimiters and control bytes.
        if self.target == "cloud" and (
            any(char in path for char in "?#%")
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
        ):
            raise SandboxError(
                f"Smol Cloud file path cannot contain ?, #, %, or control characters: {path!r}"
            )

    async def upload_file(self, sandbox_id: str, local_path: str, sandbox_path: str) -> bool:
        source = validate_upload_path(local_path)
        self._validate_cloud_file_path(sandbox_path)
        machine = await self._owned_machine(sandbox_id)
        data = await asyncio.to_thread(source.read_bytes)
        await asyncio.to_thread(machine.write_file, sandbox_path, data)
        return True

    async def download_file(self, sandbox_id: str, sandbox_path: str, local_path: str) -> bool:
        destination = validate_download_path(local_path)
        self._validate_cloud_file_path(sandbox_path)
        machine = await self._owned_machine(sandbox_id)
        data = await asyncio.to_thread(machine.read_file, sandbox_path)
        await asyncio.to_thread(Path(destination).write_bytes, data)
        return True
