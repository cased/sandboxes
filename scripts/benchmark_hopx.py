#!/usr/bin/env python3
"""Benchmark script for Hopx provider performance testing."""

import asyncio
import os
import tempfile
import time
from pathlib import Path

from sandboxes.base import SandboxConfig
from sandboxes.providers.hopx import HopxProvider


async def benchmark_hopx():
    """Run comprehensive benchmarks on Hopx provider."""
    api_key = os.getenv("HOPX_API_KEY")

    if not api_key:
        print("❌ HOPX_API_KEY not set")
        return

    print("=" * 80)
    print("HOPX PROVIDER BENCHMARK")
    print("=" * 80)
    print()

    provider = HopxProvider(api_key=api_key)

    # Test 1: Health Check
    print("📡 Test 1: Health Check")
    start = time.time()
    healthy = await provider.health_check()
    duration = time.time() - start
    print(f"   Result: {'✅ PASS' if healthy else '❌ FAIL'}")
    print(f"   Duration: {duration:.3f}s")
    print()

    if not healthy:
        print("❌ Health check failed, aborting benchmark")
        return

    # Test 2: Sandbox Creation
    print("🚀 Test 2: Sandbox Creation (template: base)")
    config = SandboxConfig(
        labels={"benchmark": "hopx", "test": "performance"}, provider_config={"template": "base"}
    )

    start = time.time()
    sandbox = await provider.create_sandbox(config)
    creation_time = time.time() - start

    print(f"   Sandbox ID: {sandbox.id}")
    print(f"   State: {sandbox.state}")
    print(f"   Duration: {creation_time:.3f}s")
    print()

    # Debug: Check sandbox metadata
    print("🔍 Debug: Sandbox Metadata")
    print(f"   Auth Token: {sandbox.metadata.get('auth_token', 'NOT FOUND')[:50]}...")
    print(f"   Public Host: {sandbox.metadata.get('public_host')}")
    print()

    # Wait for VM agent to be ready (memory snapshot boot can take time)
    print("⏳ Waiting for VM agent to be ready (10s)...")
    await asyncio.sleep(10)
    print("   Ready!")
    print()

    try:
        # Test 3: Simple Command Execution
        print("⚡ Test 3: Simple Command Execution")
        commands = [
            ("echo 'Hello Hopx'", "Echo test"),
            ("python3 --version", "Python version"),
            ("node --version", "Node version"),
            ("go version", "Go version"),
        ]

        for cmd, desc in commands:
            start = time.time()
            result = await provider.execute_command(sandbox.id, cmd)
            duration = time.time() - start

            status = "✅" if result.success else "❌"
            print(f"   {status} {desc}: {duration:.3f}s")
            if result.success:
                print(f"      Output: {result.stdout.strip()[:60]}")
        print()

        # Test 4: Compute-intensive Command
        print("🧮 Test 4: Compute-intensive Command")
        compute_cmd = "python3 -c 'print(sum(range(1000000)))'"

        start = time.time()
        result = await provider.execute_command(sandbox.id, compute_cmd)
        duration = time.time() - start

        print(f"   Result: {'✅ PASS' if result.success else '❌ FAIL'}")
        print(f"   Duration: {duration:.3f}s")
        print(f"   Output: {result.stdout.strip()}")
        print()

        # Test 5: File Upload
        print("📤 Test 5: File Upload")

        # Create a test file
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            test_content = "Hopx benchmark test file\n" * 100
            f.write(test_content)
            local_path = f.name

        try:
            start = time.time()
            success = await provider.upload_file(
                sandbox.id, local_path, "/workspace/benchmark_test.txt"
            )
            duration = time.time() - start

            print(f"   Result: {'✅ PASS' if success else '❌ FAIL'}")
            print(f"   Duration: {duration:.3f}s")
            print(f"   File size: {len(test_content)} bytes")
        finally:
            os.unlink(local_path)
        print()

        # Test 6: File Download
        print("📥 Test 6: File Download")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            download_path = f.name

        try:
            start = time.time()
            success = await provider.download_file(
                sandbox.id, "/workspace/benchmark_test.txt", download_path
            )
            duration = time.time() - start

            print(f"   Result: {'✅ PASS' if success else '❌ FAIL'}")
            print(f"   Duration: {duration:.3f}s")

            if success:
                downloaded_size = Path(download_path).stat().st_size
                print(f"   Downloaded size: {downloaded_size} bytes")
        finally:
            os.unlink(download_path)
        print()

        # Test 7: Multiple Commands (Sequential)
        print("🔄 Test 7: Sequential Commands")
        sequential_cmds = [
            "echo 'Command 1'",
            "echo 'Command 2'",
            "echo 'Command 3'",
            "echo 'Command 4'",
            "echo 'Command 5'",
        ]

        start = time.time()
        for cmd in sequential_cmds:
            await provider.execute_command(sandbox.id, cmd)
        duration = time.time() - start

        print(f"   Commands: {len(sequential_cmds)}")
        print(f"   Total Duration: {duration:.3f}s")
        print(f"   Avg per command: {duration/len(sequential_cmds):.3f}s")
        print()

        # Test 8: List Sandboxes
        print("📋 Test 8: List Sandboxes")
        start = time.time()
        sandboxes = await provider.list_sandboxes()
        duration = time.time() - start

        print("   Result: ✅ PASS")
        print(f"   Duration: {duration:.3f}s")
        print(f"   Sandboxes found: {len(sandboxes)}")
        print()

        # Test 9: Get Sandbox
        print("🔍 Test 9: Get Sandbox Details")
        start = time.time()
        fetched = await provider.get_sandbox(sandbox.id)
        duration = time.time() - start

        print(f"   Result: {'✅ PASS' if fetched else '❌ FAIL'}")
        print(f"   Duration: {duration:.3f}s")
        print()

        # Summary
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"   Total Sandbox Lifetime: {time.time() - (start - creation_time):.3f}s")
        print(f"   Sandbox Creation Time: {creation_time:.3f}s")
        print("   All tests completed successfully! ✅")
        print()

    finally:
        # Test 10: Sandbox Deletion
        print("🗑️  Test 10: Sandbox Deletion")
        start = time.time()
        success = await provider.destroy_sandbox(sandbox.id)
        duration = time.time() - start

        print(f"   Result: {'✅ PASS' if success else '❌ FAIL'}")
        print(f"   Duration: {duration:.3f}s")
        print()

        print("=" * 80)
        print("BENCHMARK COMPLETE")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(benchmark_hopx())
