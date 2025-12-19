"""Deno Deploy Sandboxes provider using the @deno/sandbox SDK via Deno runtime bridge."""

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ..base import ExecutionResult, Sandbox, SandboxConfig, SandboxProvider, SandboxState
from ..exceptions import ProviderError, SandboxError, SandboxNotFoundError
from ..security import validate_download_path, validate_upload_path

logger = logging.getLogger(__name__)

# Check if Deno is available for running the bridge script
DENO_AVAILABLE = shutil.which("deno") is not None
if not DENO_AVAILABLE:
    logger.warning(
        "Deno not available - install with: brew install deno (macOS) or see https://deno.com"
    )

# Bridge script that interfaces with @deno/sandbox (Deno ES module syntax)
BRIDGE_SCRIPT = """
import { Sandbox } from "jsr:@deno/sandbox";

// Read command from stdin
async function readStdin(): Promise<string> {
    const decoder = new TextDecoder();
    const buffer: number[] = [];

    const reader = Deno.stdin.readable.getReader();
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer.push(...value);
        }
    } finally {
        reader.releaseLock();
    }

    return decoder.decode(new Uint8Array(buffer));
}

try {
    const inputData = await readStdin();
    const command = JSON.parse(inputData);
    const result = await executeCommand(command);
    console.log(JSON.stringify(result));
} catch (error: unknown) {
    const err = error as Error;
    console.log(JSON.stringify({
        success: false,
        error: err.message || String(error)
    }));
    Deno.exit(1);
}

async function executeCommand(cmd: any): Promise<any> {
    switch (cmd.action) {
        case "create":
            return await createSandbox(cmd);
        case "execute":
            return await executeSandboxCommand(cmd);
        case "destroy":
            return await destroySandbox(cmd);
        case "list":
            return await listSandboxes(cmd);
        case "get":
            return await getSandbox(cmd);
        case "upload":
            return await uploadFile(cmd);
        case "download":
            return await downloadFile(cmd);
        case "health":
            return await healthCheck(cmd);
        default:
            throw new Error(`Unknown action: ${cmd.action}`);
    }
}

async function createSandbox(cmd: any) {
    const options: any = {};

    if (cmd.region) options.region = cmd.region;
    if (cmd.memoryMb) options.memoryMb = cmd.memoryMb;
    if (cmd.lifetime) options.lifetime = cmd.lifetime;
    if (cmd.labels) options.labels = cmd.labels;
    if (cmd.env) options.env = cmd.env;

    const sandbox = await Sandbox.create(options);

    return {
        success: true,
        sandbox: {
            id: sandbox.id,
            region: sandbox.region || null,
            createdAt: new Date().toISOString()
        }
    };
}

async function executeSandboxCommand(cmd: any) {
    const sandbox = await Sandbox.connect({ id: cmd.sandboxId });

    const startTime = Date.now();

    try {
        // Build the command with optional env vars
        let fullCommand = cmd.command;
        if (cmd.env && Object.keys(cmd.env).length > 0) {
            const envSetup = Object.entries(cmd.env)
                .map(([k, v]) => `export ${k}='${String(v).replace(/'/g, "'\\\\''")}'`)
                .join(" && ");
            fullCommand = `${envSetup} && ${cmd.command}`;
        }

        // Execute command using shell
        const result = await sandbox.runCommand(["sh", "-c", fullCommand]);

        const duration = Date.now() - startTime;

        return {
            success: true,
            result: {
                exitCode: result.code || 0,
                stdout: result.stdout || "",
                stderr: result.stderr || "",
                durationMs: duration
            }
        };
    } catch (error) {
        const duration = Date.now() - startTime;
        return {
            success: true,
            result: {
                exitCode: 1,
                stdout: "",
                stderr: error.message || String(error),
                durationMs: duration
            }
        };
    }
}

async function destroySandbox(cmd: any) {
    try {
        const sandbox = await Sandbox.connect({ id: cmd.sandboxId });
        await sandbox.kill();
        return { success: true };
    } catch (error) {
        // Sandbox might already be destroyed
        if (error.message && error.message.includes("not found")) {
            return { success: true };
        }
        throw error;
    }
}

async function listSandboxes(_cmd: any) {
    // The @deno/sandbox SDK doesn't expose list functionality directly
    // Return empty list - we track sandboxes locally in Python
    return { success: true, sandboxes: [] };
}

async function getSandbox(cmd: any) {
    try {
        const sandbox = await Sandbox.connect({ id: cmd.sandboxId });
        return {
            success: true,
            sandbox: {
                id: sandbox.id,
                region: sandbox.region || null
            }
        };
    } catch (error) {
        return { success: false, error: error.message };
    }
}

async function uploadFile(cmd: any) {
    const sandbox = await Sandbox.connect({ id: cmd.sandboxId });

    // Read local file
    const content = await Deno.readFile(cmd.localPath);

    // Write to sandbox using base64 encoding for binary safety
    const base64Content = btoa(String.fromCharCode(...content));
    await sandbox.runCommand(["sh", "-c", `echo '${base64Content}' | base64 -d > '${cmd.sandboxPath}'`]);

    return { success: true };
}

async function downloadFile(cmd: any) {
    const sandbox = await Sandbox.connect({ id: cmd.sandboxId });

    // Read from sandbox using base64 for binary safety
    const result = await sandbox.runCommand(["sh", "-c", `base64 '${cmd.sandboxPath}'`]);

    // Decode and write to local file
    const binaryString = atob(result.stdout.trim());
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }
    await Deno.writeFile(cmd.localPath, bytes);

    return { success: true };
}

async function healthCheck(_cmd: any) {
    try {
        // Try to create and immediately destroy a test sandbox
        const sandbox = await Sandbox.create({ lifetime: "1m" });
        await sandbox.kill();
        return { success: true, healthy: true };
    } catch (error) {
        return { success: true, healthy: false, error: error.message };
    }
}
"""


class DenoProvider(SandboxProvider):
    """Deno Deploy Sandboxes provider using the @deno/sandbox SDK via Deno runtime bridge."""

    def __init__(self, api_key: str | None = None, **config):
        """
        Initialize Deno provider.

        Args:
            api_key: Deno Deploy token. If not provided, reads from DENO_DEPLOY_TOKEN env var.
            **config: Additional configuration options:
                - region: Default region for sandboxes (e.g., 'ams', 'ord')
                - memory_mb: Default memory in MB (768-4096)
                - lifetime: Default lifetime (e.g., '5m', '1h')
                - deno_path: Custom path to Deno executable
        """
        super().__init__(**config)

        if not DENO_AVAILABLE:
            raise ProviderError(
                "Deno not installed - install with: brew install deno (macOS) or see https://deno.com"
            )

        self.api_key = api_key or os.getenv("DENO_DEPLOY_TOKEN")
        if not self.api_key:
            raise ProviderError(
                "Deno Deploy token not provided. "
                "Set DENO_DEPLOY_TOKEN environment variable or pass api_key parameter."
            )

        # Configuration
        self.default_region = config.get("region")
        self.default_memory_mb = config.get("memory_mb", 768)
        self.default_lifetime = config.get("lifetime", "30m")
        self.timeout = config.get("timeout", 300)
        self.deno_path = config.get("deno_path", "deno")

        # Track active sandboxes with metadata
        self._sandboxes: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

        # Bridge script path (written on first use)
        self._bridge_path: Path | None = None

    @property
    def name(self) -> str:
        """Provider name."""
        return "deno"

    def _ensure_bridge_setup(self) -> Path:
        """Ensure the Deno bridge script is written to a temp file."""
        if self._bridge_path and self._bridge_path.exists():
            return self._bridge_path

        # Create a temporary file for the bridge script
        import tempfile

        fd, path = tempfile.mkstemp(prefix="deno_sandbox_bridge_", suffix=".ts")
        os.close(fd)
        self._bridge_path = Path(path)
        self._bridge_path.write_text(BRIDGE_SCRIPT)

        logger.info(f"Deno bridge script written to {self._bridge_path}")
        return self._bridge_path

    async def _run_bridge(self, command: dict[str, Any]) -> dict[str, Any]:
        """Run a command through the Deno bridge."""
        bridge_path = self._ensure_bridge_setup()

        # Set up environment with the token
        env = {**os.environ, "DENO_DEPLOY_TOKEN": self.api_key}

        # Run the bridge script with Deno
        # -A grants all permissions, --no-check skips type checking for speed
        # --quiet suppresses download progress messages
        process = await asyncio.create_subprocess_exec(
            self.deno_path,
            "run",
            "-A",
            "--no-check",
            "--quiet",
            str(bridge_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Send command as JSON
        input_data = json.dumps(command).encode()
        stdout, stderr = await process.communicate(input=input_data)

        if process.returncode != 0:
            error_msg = (
                stderr.decode() if stderr else stdout.decode() if stdout else "Unknown error"
            )
            raise SandboxError(f"Bridge command failed: {error_msg}")

        try:
            result = json.loads(stdout.decode())
            if not result.get("success", False) and "error" in result:
                raise SandboxError(result["error"])
            return result
        except json.JSONDecodeError as e:
            raise SandboxError(f"Invalid response from bridge: {stdout.decode()}") from e

    def _to_sandbox(self, sandbox_data: dict[str, Any], metadata: dict[str, Any]) -> Sandbox:
        """Convert bridge response to standard Sandbox."""
        return Sandbox(
            id=sandbox_data["id"],
            provider=self.name,
            state=SandboxState.RUNNING,
            labels=metadata.get("labels", {}),
            created_at=metadata.get("created_at", datetime.now()),
            connection_info={
                "region": sandbox_data.get("region"),
            },
            metadata={
                "last_accessed": metadata.get("last_accessed", time.time()),
                "region": sandbox_data.get("region"),
            },
        )

    async def create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Create a new sandbox using Deno Deploy Sandboxes."""
        try:
            # Build creation options
            command = {
                "action": "create",
                "memoryMb": config.memory_mb or self.default_memory_mb,
                "lifetime": self.default_lifetime,
            }

            if config.labels:
                command["labels"] = config.labels
            if config.env_vars:
                command["env"] = config.env_vars
            if self.default_region:
                command["region"] = self.default_region

            # Handle provider-specific config
            if config.provider_config:
                if "region" in config.provider_config:
                    command["region"] = config.provider_config["region"]
                if "lifetime" in config.provider_config:
                    command["lifetime"] = config.provider_config["lifetime"]

            # Create sandbox via bridge
            result = await self._run_bridge(command)
            sandbox_data = result["sandbox"]

            # Store metadata locally
            metadata = {
                "labels": config.labels or {},
                "created_at": datetime.now(),
                "last_accessed": time.time(),
                "config": config,
                "region": sandbox_data.get("region"),
            }

            async with self._lock:
                self._sandboxes[sandbox_data["id"]] = metadata

            logger.info(f"Created Deno sandbox {sandbox_data['id']}")

            sandbox = self._to_sandbox(sandbox_data, metadata)

            # Run setup commands if provided
            if config.setup_commands:
                for cmd in config.setup_commands:
                    await self.execute_command(sandbox.id, cmd)

            return sandbox

        except Exception as e:
            logger.error(f"Failed to create Deno sandbox: {e}")
            raise SandboxError(f"Failed to create sandbox: {e}") from e

    async def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        """Get sandbox by ID."""
        # Check local tracking first
        if sandbox_id in self._sandboxes:
            metadata = self._sandboxes[sandbox_id]
            metadata["last_accessed"] = time.time()
            return self._to_sandbox({"id": sandbox_id, "region": metadata.get("region")}, metadata)

        # Try to connect via bridge
        try:
            result = await self._run_bridge({"action": "get", "sandboxId": sandbox_id})
            if result.get("success") and result.get("sandbox"):
                metadata = {
                    "labels": {},
                    "created_at": datetime.now(),
                    "last_accessed": time.time(),
                }
                async with self._lock:
                    self._sandboxes[sandbox_id] = metadata
                return self._to_sandbox(result["sandbox"], metadata)
        except Exception:
            pass

        return None

    async def list_sandboxes(self, labels: dict[str, str] | None = None) -> list[Sandbox]:
        """List active sandboxes, optionally filtered by labels."""
        sandboxes = []

        # The @deno/sandbox SDK doesn't provide a list method
        # So we can only return sandboxes we've created in this session
        for sandbox_id, metadata in self._sandboxes.items():
            # Filter by labels if provided
            if labels:
                sandbox_labels = metadata.get("labels", {})
                if not all(sandbox_labels.get(k) == v for k, v in labels.items()):
                    continue

            sandbox_data = {"id": sandbox_id, "region": metadata.get("region")}
            sandboxes.append(self._to_sandbox(sandbox_data, metadata))

        return sandboxes

    async def find_sandbox(self, labels: dict[str, str]) -> Sandbox | None:
        """Find a running sandbox with matching labels for reuse."""
        sandboxes = await self.list_sandboxes(labels=labels)
        if sandboxes:
            # Return most recently accessed
            sandboxes.sort(
                key=lambda s: self._sandboxes.get(s.id, {}).get("last_accessed", 0),
                reverse=True,
            )
            logger.info(f"Found existing sandbox {sandboxes[0].id} with labels {labels}")
            return sandboxes[0]
        return None

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute shell command in the sandbox."""
        if sandbox_id not in self._sandboxes:
            # Try to reconnect
            sandbox = await self.get_sandbox(sandbox_id)
            if not sandbox:
                raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            metadata = self._sandboxes[sandbox_id]
            metadata["last_accessed"] = time.time()

            bridge_cmd = {
                "action": "execute",
                "sandboxId": sandbox_id,
                "command": command,
            }
            if env_vars:
                bridge_cmd["env"] = env_vars

            result = await self._run_bridge(bridge_cmd)
            exec_result = result["result"]

            return ExecutionResult(
                exit_code=exec_result.get("exitCode", 0),
                stdout=exec_result.get("stdout", ""),
                stderr=exec_result.get("stderr", ""),
                duration_ms=exec_result.get("durationMs"),
                truncated=False,
                timed_out=False,
            )

        except Exception as e:
            logger.error(f"Failed to execute command in sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to execute command: {e}") from e

    async def stream_execution(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream execution output."""
        # For now, fall back to non-streaming execution
        result = await self.execute_command(sandbox_id, command, timeout, env_vars)

        # Yield output in chunks to simulate streaming
        chunk_size = 256
        output = result.stdout

        for i in range(0, len(output), chunk_size):
            yield output[i : i + chunk_size]
            await asyncio.sleep(0.01)

        if result.stderr:
            yield f"\n[stderr]: {result.stderr}"

    async def upload_file(self, sandbox_id: str, local_path: str, sandbox_path: str) -> bool:
        """Upload a file to the sandbox."""
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            validated_path = validate_upload_path(local_path)

            result = await self._run_bridge(
                {
                    "action": "upload",
                    "sandboxId": sandbox_id,
                    "localPath": str(validated_path),
                    "sandboxPath": sandbox_path,
                }
            )

            self._sandboxes[sandbox_id]["last_accessed"] = time.time()
            logger.info(f"Uploaded {validated_path} to {sandbox_path} in sandbox {sandbox_id}")
            return result.get("success", False)

        except Exception as e:
            logger.error(f"Failed to upload file to sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to upload file: {e}") from e

    async def download_file(self, sandbox_id: str, sandbox_path: str, local_path: str) -> bool:
        """Download a file from the sandbox."""
        if sandbox_id not in self._sandboxes:
            raise SandboxNotFoundError(f"Sandbox {sandbox_id} not found")

        try:
            validated_path = validate_download_path(local_path)

            result = await self._run_bridge(
                {
                    "action": "download",
                    "sandboxId": sandbox_id,
                    "sandboxPath": sandbox_path,
                    "localPath": str(validated_path),
                }
            )

            self._sandboxes[sandbox_id]["last_accessed"] = time.time()
            logger.info(f"Downloaded {sandbox_path} from sandbox {sandbox_id} to {validated_path}")
            return result.get("success", False)

        except Exception as e:
            logger.error(f"Failed to download file from sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to download file: {e}") from e

    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox."""
        try:
            result = await self._run_bridge({"action": "destroy", "sandboxId": sandbox_id})

            # Remove from tracking
            if sandbox_id in self._sandboxes:
                async with self._lock:
                    del self._sandboxes[sandbox_id]

            logger.info(f"Destroyed Deno sandbox {sandbox_id}")
            return result.get("success", False)

        except Exception as e:
            logger.error(f"Failed to destroy sandbox {sandbox_id}: {e}")
            raise SandboxError(f"Failed to destroy sandbox: {e}") from e

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

            if stop_on_error and not result.success:
                logger.warning(f"Command failed, stopping sequence: {command}")
                break

        return results

    async def get_or_create_sandbox(self, config: SandboxConfig) -> Sandbox:
        """Get existing sandbox with matching labels or create new one."""
        if config.labels:
            existing = await self.find_sandbox(config.labels)
            if existing:
                return existing

        return await self.create_sandbox(config)

    async def health_check(self) -> bool:
        """Check if Deno service is accessible."""
        try:
            result = await self._run_bridge({"action": "health"})
            return result.get("healthy", False)
        except Exception as e:
            logger.error(f"Deno health check failed: {e}")
            return False

    async def cleanup_idle_sandboxes(self, idle_timeout: int = 600):
        """Clean up sandboxes that have been idle."""
        current_time = time.time()
        to_destroy = []

        for sandbox_id, metadata in self._sandboxes.items():
            last_accessed = metadata.get("last_accessed", current_time)
            if current_time - last_accessed > idle_timeout:
                to_destroy.append(sandbox_id)

        for sandbox_id in to_destroy:
            logger.info(f"Cleaning up idle sandbox {sandbox_id}")
            await self.destroy_sandbox(sandbox_id)

    def __del__(self):
        """Cleanup on deletion."""
        import contextlib

        # Clean up temporary bridge script file
        if self._bridge_path and self._bridge_path.exists():
            with contextlib.suppress(Exception):
                self._bridge_path.unlink()
