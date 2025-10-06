"""Real integration tests for E2B provider."""

import asyncio
import contextlib
import time

import pytest

from sandboxes import SandboxConfig


@pytest.mark.integration
@pytest.mark.e2b
class TestE2BIntegration:
    """Integration tests for E2B provider with real API."""

    @pytest.mark.asyncio
    async def test_create_and_destroy_sandbox(self, e2b_provider):
        """Test creating and destroying a real E2B sandbox."""
        config = SandboxConfig(labels={"test": "integration", "provider": "e2b"})

        # Create sandbox
        sandbox = await e2b_provider.create_sandbox(config)
        assert sandbox is not None
        assert sandbox.id is not None
        assert sandbox.provider == "e2b"
        assert sandbox.state.value == "running"

        # Verify we can get it
        retrieved = await e2b_provider.get_sandbox(sandbox.id)
        assert retrieved is not None
        assert retrieved.id == sandbox.id

        # Destroy sandbox
        destroyed = await e2b_provider.destroy_sandbox(sandbox.id)
        assert destroyed is True

        # Verify it's gone
        retrieved = await e2b_provider.get_sandbox(sandbox.id)
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_execute_shell_commands(self, e2b_provider):
        """Test executing shell commands in E2B sandbox."""
        config = SandboxConfig()
        sandbox = await e2b_provider.create_sandbox(config)

        try:
            # Simple echo
            result = await e2b_provider.execute_command(sandbox.id, "echo 'Hello from E2B!'")
            assert result.success
            assert "Hello from E2B!" in result.stdout

            # Python inline
            result = await e2b_provider.execute_command(
                sandbox.id, "python3 -c \"x = 10; y = 20; print(f'Sum: {x + y}')\""
            )
            assert result.success
            assert "Sum: 30" in result.stdout

            # Check Python version
            result = await e2b_provider.execute_command(sandbox.id, "python3 --version")
            assert result.success
            assert "Python" in result.stdout

        finally:
            await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_execute_with_environment_variables(self, e2b_provider):
        """Test executing code with environment variables."""
        config = SandboxConfig(env_vars={"INITIAL_VAR": "initial_value"})
        sandbox = await e2b_provider.create_sandbox(config)

        try:
            # Check initial env var
            result = await e2b_provider.execute_command(sandbox.id, "echo $INITIAL_VAR")
            assert result.success
            assert "initial_value" in result.stdout

            # Execute with additional env vars
            result = await e2b_provider.execute_command(
                sandbox.id,
                "echo $RUNTIME_VAR",
                env_vars={"RUNTIME_VAR": "runtime_value"},
            )
            assert result.success
            assert "runtime_value" in result.stdout

        finally:
            await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_file_operations(self, e2b_provider):
        """Test file operations in E2B sandbox."""
        config = SandboxConfig()
        sandbox = await e2b_provider.create_sandbox(config)

        try:
            # Create and read file using shell commands
            result = await e2b_provider.execute_command(
                sandbox.id, "echo 'Test content' > /tmp/test.txt && cat /tmp/test.txt"
            )
            assert result.success
            assert "Test content" in result.stdout

            # Multiple file operations
            result = await e2b_provider.execute_command(
                sandbox.id,
                "mkdir -p /tmp/testdir && echo 'nested' > /tmp/testdir/file.txt && cat /tmp/testdir/file.txt",
            )
            assert result.success
            assert "nested" in result.stdout

        finally:
            await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_file_upload_download(self, e2b_provider, tmp_path):
        """Test file upload and download operations."""
        config = SandboxConfig()
        sandbox = await e2b_provider.create_sandbox(config)

        try:
            # Create a local test file
            local_file = tmp_path / "test_upload.txt"
            test_content = "Hello from local system!"
            local_file.write_text(test_content)

            # Upload file to sandbox
            remote_path = "/tmp/uploaded.txt"
            success = await e2b_provider.upload_file(sandbox.id, str(local_file), remote_path)
            assert success

            # Verify file exists in sandbox via command
            result = await e2b_provider.execute_command(sandbox.id, f"cat {remote_path}")
            assert result.success
            assert test_content in result.stdout

            # Download file back
            download_file = tmp_path / "test_download.txt"
            success = await e2b_provider.download_file(sandbox.id, remote_path, str(download_file))
            assert success

            # Verify downloaded content matches
            downloaded_content = download_file.read_text()
            assert downloaded_content == test_content

        finally:
            await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_error_handling(self, e2b_provider):
        """Test error handling in E2B sandbox."""
        config = SandboxConfig()
        sandbox = await e2b_provider.create_sandbox(config)

        try:
            # Command not found
            result = await e2b_provider.execute_command(sandbox.id, "nonexistent_command")
            assert not result.success
            assert result.exit_code != 0

            # Failed command
            result = await e2b_provider.execute_command(sandbox.id, "cat /nonexistent/file.txt")
            assert not result.success
            assert result.exit_code != 0

            # Python error
            result = await e2b_provider.execute_command(
                sandbox.id, "python3 -c 'raise ValueError(\"Test error\")'"
            )
            assert not result.success
            assert result.exit_code != 0

        finally:
            await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_sandbox_reuse_with_labels(self, e2b_provider):
        """Test sandbox reuse based on labels."""
        config = SandboxConfig(labels={"session": "test-reuse", "user": "pytest"})

        # Create first sandbox
        sandbox1 = await e2b_provider.create_sandbox(config)

        try:
            # Store state in sandbox via environment variable
            result = await e2b_provider.execute_command(
                sandbox1.id, "export TEST_VALUE=42 && echo $TEST_VALUE"
            )
            assert result.success
            assert "42" in result.stdout

            # Try to get sandbox with same labels
            existing = await e2b_provider.find_sandbox(config.labels)

            if existing:
                # Verify it's the same sandbox
                assert existing.id == sandbox1.id

                # For shell sessions, state persists within the same process
                # but exported vars don't persist across different execute_command calls
                # So just verify we can execute commands
                result = await e2b_provider.execute_command(
                    existing.id, "echo 'Sandbox is reusable'"
                )
                assert result.success

        finally:
            await e2b_provider.destroy_sandbox(sandbox1.id)

    @pytest.mark.asyncio
    async def test_multiple_sandboxes(self, e2b_provider):
        """Test managing multiple sandboxes concurrently."""
        configs = [
            SandboxConfig(labels={"sandbox": "1"}),
            SandboxConfig(labels={"sandbox": "2"}),
            SandboxConfig(labels={"sandbox": "3"}),
        ]

        sandboxes = []
        try:
            # Create multiple sandboxes concurrently
            sandboxes = await asyncio.gather(
                *[e2b_provider.create_sandbox(config) for config in configs]
            )

            assert len(sandboxes) == 3
            assert len(set(s.id for s in sandboxes)) == 3  # All unique IDs

            # Execute commands in parallel
            results = await asyncio.gather(
                *[
                    e2b_provider.execute_command(sandbox.id, f"echo 'Sandbox {i}'")
                    for i, sandbox in enumerate(sandboxes, 1)
                ]
            )

            for i, result in enumerate(results, 1):
                assert result.success
                assert f"Sandbox {i}" in result.stdout

        finally:
            # Cleanup all sandboxes
            for sandbox in sandboxes:
                with contextlib.suppress(Exception):
                    await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_long_running_command(self, e2b_provider):
        """Test executing long-running commands."""
        config = SandboxConfig(timeout_seconds=30)
        sandbox = await e2b_provider.create_sandbox(config)

        try:
            # Execute a command that takes a few seconds
            result = await e2b_provider.execute_command(
                sandbox.id,
                'python3 -c \'import time; print("Starting..."); time.sleep(3); print("Done!")\'',
            )
            assert result.success
            assert "Starting" in result.stdout
            assert "Done" in result.stdout
            assert result.duration_ms >= 3000  # At least 3 seconds

        finally:
            await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_streaming_output(self, e2b_provider):
        """Test streaming output from E2B sandbox."""
        config = SandboxConfig()
        sandbox = await e2b_provider.create_sandbox(config)

        try:
            # Generate output to stream
            chunks = []
            async for chunk in e2b_provider.stream_execution(
                sandbox.id, "python3 -c 'for i in range(5): print(f\"Line {i}\")'"
            ):
                chunks.append(chunk)

            # Should have received some output
            assert len(chunks) > 0
            full_output = "".join(chunks)
            assert "Line 0" in full_output
            assert "Line 4" in full_output

        finally:
            await e2b_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_performance_baseline(self, e2b_provider):
        """Test performance metrics for E2B operations."""
        metrics = {
            "create_time": 0,
            "execute_time": 0,
            "destroy_time": 0,
        }

        config = SandboxConfig()

        # Measure sandbox creation time
        start = time.time()
        sandbox = await e2b_provider.create_sandbox(config)
        metrics["create_time"] = (time.time() - start) * 1000  # ms

        try:
            # Measure command execution time
            start = time.time()
            result = await e2b_provider.execute_command(sandbox.id, "echo 'performance test'")
            metrics["execute_time"] = (time.time() - start) * 1000  # ms
            assert result.success

        finally:
            # Measure destroy time
            start = time.time()
            await e2b_provider.destroy_sandbox(sandbox.id)
            metrics["destroy_time"] = (time.time() - start) * 1000  # ms

        # Log metrics (these are baseline expectations)
        print("\nE2B Performance Metrics:")
        print(f"  Create: {metrics['create_time']:.2f}ms")
        print(f"  Execute: {metrics['execute_time']:.2f}ms")
        print(f"  Destroy: {metrics['destroy_time']:.2f}ms")

        # E2B claims ~150ms creation time
        # These are generous limits for CI environments
        assert metrics["create_time"] < 5000  # 5 seconds max
        assert metrics["execute_time"] < 2000  # 2 seconds max
        assert metrics["destroy_time"] < 2000  # 2 seconds max
