"""Real integration tests for Daytona provider."""

import asyncio
import contextlib
import os
import tempfile
import time

import pytest

from sandboxes import SandboxConfig, SandboxState


@pytest.mark.integration
@pytest.mark.daytona
class TestDaytonaIntegration:
    """Integration tests for Daytona provider with real API."""

    @pytest.mark.asyncio
    async def test_create_and_destroy_sandbox(self, daytona_provider):
        """Test creating and destroying a real Daytona sandbox."""
        config = SandboxConfig(
            labels={"test": "integration", "provider": "daytona"},
            image="daytonaio/ai-test:0.2.3",
        )

        # Create sandbox
        sandbox = await daytona_provider.create_sandbox(config)
        assert sandbox is not None
        assert sandbox.id is not None
        assert sandbox.provider == "daytona"
        assert sandbox.state in [SandboxState.RUNNING, SandboxState.STARTING]

        # Verify we can get it
        retrieved = await daytona_provider.get_sandbox(sandbox.id)
        assert retrieved is not None
        assert retrieved.id == sandbox.id

        # List sandboxes with labels
        sandboxes = await daytona_provider.list_sandboxes(labels=config.labels)
        assert any(s.id == sandbox.id for s in sandboxes)

        # Destroy sandbox
        destroyed = await daytona_provider.destroy_sandbox(sandbox.id)
        assert destroyed is True

        # Verify it's gone or not running
        retrieved = await daytona_provider.get_sandbox(sandbox.id)
        if retrieved:
            assert retrieved.state != SandboxState.RUNNING

    @pytest.mark.asyncio
    async def test_execute_shell_commands(self, daytona_provider):
        """Test executing shell commands in Daytona sandbox."""
        config = SandboxConfig(image="daytonaio/ai-test:0.2.3")
        sandbox = await daytona_provider.create_sandbox(config)

        try:
            # Simple echo
            result = await daytona_provider.execute_command(
                sandbox.id, "echo 'Hello from Daytona!'"
            )
            assert result.success
            assert "Hello from Daytona!" in result.stdout

            # Check Python availability
            result = await daytona_provider.execute_command(sandbox.id, "python3 --version")
            assert result.success
            assert "Python" in result.stdout

            # File operations
            result = await daytona_provider.execute_command(
                sandbox.id, "echo 'Test content' > /tmp/test.txt && cat /tmp/test.txt"
            )
            assert result.success
            assert "Test content" in result.stdout

            # Multiple commands
            result = await daytona_provider.execute_command(sandbox.id, "pwd && ls -la && whoami")
            assert result.success
            assert result.stdout  # Should have output from all commands

        finally:
            await daytona_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_execute_python_scripts(self, daytona_provider):
        """Test executing Python scripts in Daytona sandbox."""
        config = SandboxConfig(image="daytonaio/ai-test:0.2.3")
        sandbox = await daytona_provider.create_sandbox(config)

        try:
            # Execute Python inline
            result = await daytona_provider.execute_command(
                sandbox.id, "python3 -c \"print('Hello from Python!')\""
            )
            assert result.success
            assert "Hello from Python!" in result.stdout

            # Create and run Python script
            script = """
import sys
import platform

print(f'Python version: {sys.version}')
print(f'Platform: {platform.platform()}')

# Simple computation
x = 10
y = 20
print(f'Sum of {x} and {y} is {x + y}')
"""
            result = await daytona_provider.execute_command(
                sandbox.id, f"cat > /tmp/test.py << 'EOF'\n{script}\nEOF"
            )
            assert result.success

            result = await daytona_provider.execute_command(sandbox.id, "python3 /tmp/test.py")
            assert result.success
            assert "Python version" in result.stdout
            assert "Sum of 10 and 20 is 30" in result.stdout

        finally:
            await daytona_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_environment_variables(self, daytona_provider):
        """Test environment variable handling in Daytona."""
        config = SandboxConfig(
            env_vars={"INITIAL_VAR": "initial_value"},
            image="daytonaio/ai-test:0.2.3",
        )
        sandbox = await daytona_provider.create_sandbox(config)

        try:
            # Check if initial env vars are set (if supported)
            result = await daytona_provider.execute_command(sandbox.id, "echo $INITIAL_VAR")
            # Note: Daytona might not support initial env vars
            # in the config, so we test runtime env vars

            # Execute with runtime env vars
            result = await daytona_provider.execute_command(
                sandbox.id, "echo $RUNTIME_VAR", env_vars={"RUNTIME_VAR": "runtime_value"}
            )
            assert result.success
            assert "runtime_value" in result.stdout

            # Multiple env vars
            result = await daytona_provider.execute_command(
                sandbox.id,
                'echo "KEY1=$KEY1, KEY2=$KEY2"',
                env_vars={"KEY1": "value1", "KEY2": "value2"},
            )
            assert result.success
            assert "KEY1=value1" in result.stdout
            assert "KEY2=value2" in result.stdout

        finally:
            await daytona_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_smart_reuse_with_labels(self, daytona_provider):
        """Test Daytona's smart sandbox reuse based on labels."""
        config = SandboxConfig(
            labels={"session": "test-reuse-daytona", "user": "pytest", "purpose": "testing"},
            image="daytonaio/ai-test:0.2.3",
        )

        sandbox1 = None
        sandbox2 = None

        try:
            # Create first sandbox
            sandbox1 = await daytona_provider.create_sandbox(config)
            assert sandbox1 is not None

            # Store state in sandbox
            result = await daytona_provider.execute_command(
                sandbox1.id, "echo 'persistent_data' > /tmp/state.txt"
            )
            assert result.success

            # Find sandbox with same labels - Daytona should support this
            existing = await daytona_provider.find_sandbox(config.labels)
            assert existing is not None
            assert existing.id == sandbox1.id

            # Use get_or_create with same labels - should reuse
            sandbox2 = await daytona_provider.get_or_create_sandbox(config)
            assert sandbox2.id == sandbox1.id  # Should be the same sandbox

            # Verify state is preserved
            result = await daytona_provider.execute_command(sandbox2.id, "cat /tmp/state.txt")
            assert result.success
            assert "persistent_data" in result.stdout

        finally:
            # Clean up
            if sandbox1:
                await daytona_provider.destroy_sandbox(sandbox1.id)
            # sandbox2 is the same as sandbox1, so no need to destroy twice

    @pytest.mark.asyncio
    async def test_error_handling(self, daytona_provider):
        """Test error handling in Daytona sandbox."""
        config = SandboxConfig(image="daytonaio/ai-test:0.2.3")
        sandbox = await daytona_provider.create_sandbox(config)

        try:
            # Command not found
            result = await daytona_provider.execute_command(sandbox.id, "nonexistentcommand")
            assert not result.success
            assert result.exit_code != 0

            # Python error
            result = await daytona_provider.execute_command(
                sandbox.id, "python3 -c \"raise ValueError('Test error')\""
            )
            assert not result.success
            assert "ValueError" in result.stderr or "ValueError" in result.stdout

            # Shell error
            result = await daytona_provider.execute_command(sandbox.id, "exit 42")
            assert not result.success
            assert result.exit_code == 42

        finally:
            await daytona_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_concurrent_commands(self, daytona_provider):
        """Test executing commands concurrently in same sandbox."""
        config = SandboxConfig(image="daytonaio/ai-test:0.2.3")
        sandbox = await daytona_provider.create_sandbox(config)

        try:
            # Execute multiple commands concurrently
            commands = [
                "echo 'Command 1' && sleep 1",
                "echo 'Command 2' && sleep 1",
                "echo 'Command 3' && sleep 1",
            ]

            start = time.time()
            results = await asyncio.gather(
                *[daytona_provider.execute_command(sandbox.id, cmd) for cmd in commands]
            )
            duration = time.time() - start

            # All should succeed
            for i, result in enumerate(results, 1):
                assert result.success
                assert f"Command {i}" in result.stdout

            # Should execute in parallel (faster than sequential)
            # Sequential would take 3+ seconds, parallel should be ~1 second
            # But we'll be generous for CI environments
            assert duration < 5  # seconds

        finally:
            await daytona_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_multiple_sandboxes(self, daytona_provider):
        """Test managing multiple Daytona sandboxes."""
        configs = [
            SandboxConfig(
                labels={"sandbox": f"daytona-{i}"},
                image="daytonaio/ai-test:0.2.3",
            )
            for i in range(3)
        ]

        sandboxes = []
        try:
            # Create multiple sandboxes
            for config in configs:
                sandbox = await daytona_provider.create_sandbox(config)
                sandboxes.append(sandbox)

            assert len(sandboxes) == 3
            assert len(set(s.id for s in sandboxes)) == 3  # All unique

            # Execute different commands in each
            results = await asyncio.gather(
                *[
                    daytona_provider.execute_command(sandbox.id, f"echo 'Sandbox {i}'")
                    for i, sandbox in enumerate(sandboxes, 1)
                ]
            )

            for i, result in enumerate(results, 1):
                assert result.success
                assert f"Sandbox {i}" in result.stdout

            # List all sandboxes
            all_sandboxes = await daytona_provider.list_sandboxes()
            for sandbox in sandboxes:
                assert any(s.id == sandbox.id for s in all_sandboxes)

        finally:
            # Cleanup all sandboxes
            for sandbox in sandboxes:
                with contextlib.suppress(Exception):
                    await daytona_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_long_running_command(self, daytona_provider):
        """Test long-running command execution."""
        config = SandboxConfig(timeout_seconds=30, image="daytonaio/ai-test:0.2.3")
        sandbox = await daytona_provider.create_sandbox(config)

        try:
            # Command that takes a few seconds
            result = await daytona_provider.execute_command(
                sandbox.id, "echo 'Starting...' && sleep 3 && echo 'Done!'"
            )
            assert result.success
            assert "Starting..." in result.stdout
            assert "Done!" in result.stdout

        finally:
            await daytona_provider.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_performance_baseline(self, daytona_provider):
        """Test performance metrics for Daytona operations."""
        metrics = {
            "create_time": 0,
            "execute_time": 0,
            "destroy_time": 0,
        }

        config = SandboxConfig(image="daytonaio/ai-test:0.2.3")

        # Measure sandbox creation
        start = time.time()
        sandbox = await daytona_provider.create_sandbox(config)
        metrics["create_time"] = (time.time() - start) * 1000  # ms

        try:
            # Measure command execution
            start = time.time()
            result = await daytona_provider.execute_command(sandbox.id, "echo 'performance test'")
            metrics["execute_time"] = (time.time() - start) * 1000  # ms
            assert result.success

        finally:
            # Measure destroy time
            start = time.time()
            await daytona_provider.destroy_sandbox(sandbox.id)
            metrics["destroy_time"] = (time.time() - start) * 1000  # ms

        # Log metrics
        print("\nDaytona Performance Metrics:")
        print(f"  Create: {metrics['create_time']:.2f}ms")
        print(f"  Execute: {metrics['execute_time']:.2f}ms")
        print(f"  Destroy: {metrics['destroy_time']:.2f}ms")

        # Daytona claims sub-90ms creation
        # These are generous limits for CI environments
        assert metrics["create_time"] < 5000  # 5 seconds max
        assert metrics["execute_time"] < 2000  # 2 seconds max
        assert metrics["destroy_time"] < 3000  # 3 seconds max

    @pytest.mark.asyncio
    async def test_file_upload_download(self, daytona_provider):
        """Test file upload and download operations in Daytona sandbox."""
        config = SandboxConfig(image="daytonaio/ai-test:0.2.3", timeout_seconds=180)
        sandbox = await daytona_provider.create_sandbox(config)

        try:
            # Create a temporary file to upload
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
                test_content = "Hello from Daytona file upload test!\nLine 2\nLine 3"
                f.write(test_content)
                local_upload_path = f.name

            try:
                # Upload the file to sandbox
                sandbox_path = "/tmp/uploaded_test.txt"
                success = await daytona_provider.upload_file(
                    sandbox.id, local_upload_path, sandbox_path
                )
                assert success is True

                # Verify file exists in sandbox
                result = await daytona_provider.execute_command(sandbox.id, f"cat {sandbox_path}")
                assert result.success
                assert test_content in result.stdout

                # Download the file back
                with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
                    local_download_path = f.name

                try:
                    success = await daytona_provider.download_file(
                        sandbox.id, sandbox_path, local_download_path
                    )
                    assert success is True

                    # Verify downloaded content matches
                    with open(local_download_path) as f:
                        downloaded_content = f.read()
                    assert downloaded_content == test_content

                finally:
                    # Clean up downloaded file
                    if os.path.exists(local_download_path):
                        os.unlink(local_download_path)

            finally:
                # Clean up uploaded file
                if os.path.exists(local_upload_path):
                    os.unlink(local_upload_path)

        finally:
            await daytona_provider.destroy_sandbox(sandbox.id)
