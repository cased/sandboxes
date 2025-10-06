#!/usr/bin/env python
"""Test sandbox providers with generic operations.

Usage:
    python scripts/test_providers.py           # Test all providers
    python scripts/test_providers.py modal     # Test specific provider
    python scripts/test_providers.py e2b
    python scripts/test_providers.py daytona
"""

import asyncio
import os
import sys
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandboxes import SandboxConfig
from sandboxes.providers.daytona import DaytonaProvider
from sandboxes.providers.e2b import E2BProvider
from sandboxes.providers.modal import ModalProvider


async def test_provider(provider_name: str, provider):
    """Test a single provider with standard operations."""
    print(f"\n{'='*60}")
    print(f"Testing {provider_name}")
    print(f"{'='*60}")

    try:
        # Create sandbox with appropriate config
        config = SandboxConfig(labels={"test": "provider_test"})

        if provider_name == "Modal":
            config.image = "python:3.11-slim"
        elif provider_name == "E2B":
            pass  # Uses default template
        elif provider_name == "Daytona":
            config.image = "command-execution-env"

        print("Creating sandbox...")
        sandbox = await provider.create_sandbox(config)
        print(f"✅ Sandbox created: {sandbox.id[:30]}...")

        # Test cases
        tests_passed = 0
        tests_failed = 0

        test_commands = [
            ("Echo test", "echo 'Hello from sandbox'"),
            ("Python version", "python3 --version || python --version"),
            ("List files", "ls -la /tmp"),
            ("Create file", "echo 'test data' > /tmp/test.txt && cat /tmp/test.txt"),
            ("System info", "uname -a"),
            ("Environment", "env | head -5"),
            ("Process list", "ps aux | head -5"),
            ("Network test", "ping -c 1 8.8.8.8 || echo 'Network test'"),
            ("Python calculation", "python3 -c 'print(f\"Result: {sum(range(100))}\")'"),
            (
                "Install package",
                "pip install --quiet requests && python3 -c 'import requests; print(f\"Requests {requests.__version__}\")'",
            ),
        ]

        print(f"\nRunning {len(test_commands)} test commands...")
        print("-" * 40)

        for test_name, command in test_commands:
            try:
                result = await provider.execute_command(sandbox.id, command)
                if result.success:
                    print(f"✅ {test_name:20} - Success")
                    if result.stdout and len(result.stdout.strip()) < 100:
                        print(f"   Output: {result.stdout.strip()[:100]}")
                    tests_passed += 1
                else:
                    print(f"❌ {test_name:20} - Failed (exit code: {result.exit_code})")
                    if result.stderr:
                        print(f"   Error: {result.stderr.strip()[:100]}")
                    tests_failed += 1
            except Exception as e:
                print(f"❌ {test_name:20} - Exception: {str(e)[:100]}")
                tests_failed += 1

        # Clean up
        print("\nDestroying sandbox...")
        destroyed = await provider.destroy_sandbox(sandbox.id)
        if destroyed:
            print("✅ Sandbox destroyed")
        else:
            print("⚠️  Failed to destroy sandbox")

        # Summary
        print(f"\n{'='*40}")
        print(f"Results for {provider_name}:")
        print(f"  Passed: {tests_passed}/{len(test_commands)}")
        print(f"  Failed: {tests_failed}/{len(test_commands)}")
        print(f"  Success rate: {(tests_passed/len(test_commands)*100):.1f}%")

        return tests_passed, tests_failed

    except Exception as e:
        print(f"❌ Fatal error testing {provider_name}: {e}")
        return 0, len(test_commands) if "test_commands" in locals() else 1


async def main(provider_filter: Optional[str] = None):
    """Test all providers or a specific one."""
    print("🔬 SANDBOX PROVIDER TEST SUITE")
    print("=" * 60)

    providers_to_test = []

    # Modal
    if not provider_filter or provider_filter.lower() == "modal":
        try:
            modal = ModalProvider()
            providers_to_test.append(("Modal", modal))
            print("✅ Modal provider available")
        except Exception as e:
            print(f"❌ Modal not available: {e}")

    # E2B
    if not provider_filter or provider_filter.lower() == "e2b":
        if os.getenv("E2B_API_KEY"):
            try:
                e2b = E2BProvider()
                providers_to_test.append(("E2B", e2b))
                print("✅ E2B provider available")
            except Exception as e:
                print(f"❌ E2B not available: {e}")
        else:
            print("⚠️  E2B_API_KEY not set")

    # Daytona
    if not provider_filter or provider_filter.lower() == "daytona":
        if os.getenv("DAYTONA_API_KEY"):
            try:
                daytona = DaytonaProvider()
                providers_to_test.append(("Daytona", daytona))
                print("✅ Daytona provider available")
            except Exception as e:
                print(f"❌ Daytona not available: {e}")
        else:
            print("⚠️  DAYTONA_API_KEY not set")

    if not providers_to_test:
        print("\n❌ No providers available to test")
        print("Please configure at least one provider:")
        print("  - Modal: Run 'modal token set'")
        print("  - E2B: Set E2B_API_KEY environment variable")
        print("  - Daytona: Set DAYTONA_API_KEY environment variable")
        return

    # Test each provider
    total_passed = 0
    total_failed = 0

    for name, provider in providers_to_test:
        passed, failed = await test_provider(name, provider)
        total_passed += passed
        total_failed += failed

    # Final summary
    print(f"\n{'='*60}")
    print("📊 OVERALL SUMMARY")
    print(f"{'='*60}")
    print(f"Providers tested: {len(providers_to_test)}")
    print(f"Total tests passed: {total_passed}")
    print(f"Total tests failed: {total_failed}")
    if total_passed + total_failed > 0:
        print(f"Overall success rate: {(total_passed/(total_passed+total_failed)*100):.1f}%")


if __name__ == "__main__":
    import sys

    provider_filter = sys.argv[1] if len(sys.argv) > 1 else None

    if provider_filter and provider_filter in ["-h", "--help", "help"]:
        print(__doc__)
    else:
        asyncio.run(main(provider_filter))
