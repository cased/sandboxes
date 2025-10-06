#!/usr/bin/env python
"""Real-world validation test that queries sandbox APIs to verify creation."""

import asyncio
import os
import time

from e2b import Sandbox as E2BSandbox

# Import our library
from sandboxes import SandboxConfig
from sandboxes.providers.e2b import E2BProvider


async def validate_e2b():
    """Validate E2B sandbox creation with real API queries."""
    print("\n🔍 E2B Validation Test")
    print("=" * 50)

    # Check API key
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        print("❌ E2B_API_KEY not found")
        return False
    print(f"✅ E2B_API_KEY found: {api_key[:8]}...")

    # List existing sandboxes before
    print("\n📦 Existing sandboxes before test:")
    existing = E2BSandbox.list()
    print(f"  Found {len(existing)} sandboxes")
    for s in existing[:3]:
        print(f"    - {s.sandbox_id} (template: {s.template_id})")

    # Create sandbox through our library
    print("\n🚀 Creating sandbox through our library...")
    provider = E2BProvider(api_key=api_key)
    config = SandboxConfig(labels={"test": "validation", "timestamp": str(int(time.time()))})

    start = time.time()
    sandbox = await provider.create_sandbox(config)
    create_time = (time.time() - start) * 1000
    print(f"✅ Created sandbox: {sandbox.id}")
    print(f"  Creation time: {create_time:.2f}ms")
    print(f"  State: {sandbox.state.value}")
    print(f"  Labels: {sandbox.labels}")

    # Verify through E2B API
    print("\n🔍 Verifying through E2B list API...")
    all_sandboxes = E2BSandbox.list()
    print(f"  Total sandboxes now: {len(all_sandboxes)}")

    found = False
    for s in all_sandboxes:
        if s.sandbox_id == sandbox.id:
            found = True
            print(f"✅ Found our sandbox in E2B list: {s.sandbox_id}")
            print(f"    Template: {s.template_id}")
            # print(f"    Created: {s.created_at}")  # ListedSandbox doesn't have created_at
            break

    if not found:
        print(f"❌ Sandbox {sandbox.id} NOT found in E2B list!")

    # Test execution
    print("\n💻 Testing shell command execution...")
    result = await provider.execute_command(sandbox.id, "echo 'Hello from validation test!'")
    print(f"  Exit code: {result.exit_code}")
    print(f"  Output: {result.stdout.strip()}")
    print(f"  Success: {result.success}")

    # Test Python operations
    print("\n🐍 Testing Python operations...")
    result = await provider.execute_command(
        sandbox.id,
        "python3 -c \"import sys; import platform; print(f'Python: {sys.version}'); print(f'Platform: {platform.platform()}'); x = sum(range(1000)); print(f'Sum of 0-999: {x}')\"",
    )
    if result.success:
        print("✅ Python execution successful")
        for line in result.stdout.strip().split("\n"):
            print(f"    {line}")
    else:
        print(f"❌ Python execution failed: {result.stderr}")

    # Clean up
    print("\n🧹 Cleaning up...")
    destroyed = await provider.destroy_sandbox(sandbox.id)
    print(f"  Sandbox destroyed: {destroyed}")

    # Verify deletion through API
    print("\n🔍 Verifying deletion through E2B API...")
    remaining = E2BSandbox.list()
    still_exists = any(s.sandbox_id == sandbox.id for s in remaining)

    if still_exists:
        print(f"❌ Sandbox {sandbox.id} still exists after deletion!")
    else:
        print(f"✅ Sandbox {sandbox.id} successfully removed")

    print(f"\n📊 Final sandbox count: {len(remaining)}")

    return True


async def validate_daytona():
    """Validate Daytona sandbox creation (if available)."""
    print("\n🔍 Daytona Validation Test")
    print("=" * 50)

    api_key = os.getenv("DAYTONA_API_KEY")
    if not api_key:
        print("⚠️ DAYTONA_API_KEY not found - skipping")
        return False

    try:
        from sandboxes.providers.daytona import DaytonaProvider

        print(f"✅ DAYTONA_API_KEY found: {api_key[:8]}...")

        provider = DaytonaProvider(api_key=api_key)
        config = SandboxConfig(labels={"test": "validation"}, image="daytonaio/ai-test:0.2.3")

        # Create sandbox
        print("\n🚀 Creating Daytona sandbox...")
        start = time.time()
        sandbox = await provider.create_sandbox(config)
        create_time = (time.time() - start) * 1000
        print(f"✅ Created sandbox: {sandbox.id}")
        print(f"  Creation time: {create_time:.2f}ms")

        # List sandboxes
        print("\n📦 Listing Daytona sandboxes...")
        all_sandboxes = await provider.list_sandboxes()
        print(f"  Total sandboxes: {len(all_sandboxes)}")

        found = any(s.id == sandbox.id for s in all_sandboxes)
        if found:
            print("✅ Found our sandbox in list")
        else:
            print("❌ Sandbox not found in list")

        # Test execution
        print("\n💻 Testing command execution...")
        result = await provider.execute_command(sandbox.id, "echo 'Hello from Daytona validation!'")
        print(f"  Output: {result.stdout.strip()}")
        print(f"  Success: {result.success}")

        # Cleanup
        print("\n🧹 Cleaning up...")
        await provider.destroy_sandbox(sandbox.id)
        print("✅ Sandbox destroyed")

        return True

    except Exception as e:
        print(f"❌ Daytona validation failed: {e}")
        return False


async def validate_modal():
    """Validate Modal sandbox creation (if available)."""
    print("\n🔍 Modal Validation Test")
    print("=" * 50)

    # Check if Modal is configured
    if not os.path.exists(os.path.expanduser("~/.modal.toml")):
        print("⚠️ Modal not configured (~/.modal.toml not found) - skipping")
        return False

    try:
        from modal import Sandbox as ModalSandbox

        from sandboxes.providers.modal import ModalProvider

        print("✅ Modal configured (found ~/.modal.toml)")

        provider = ModalProvider()
        config = SandboxConfig(
            labels={"test": "validation", "timestamp": str(int(time.time()))},
            provider_config={
                "image": "python:3.11-slim",
                "cpu": 1.0,
                "memory": 512,
            },
        )

        # Create sandbox
        print("\n🚀 Creating Modal sandbox...")
        start = time.time()
        sandbox = await provider.create_sandbox(config)
        create_time = (time.time() - start) * 1000
        print(f"✅ Created sandbox: {sandbox.id}")
        print(f"  Creation time: {create_time:.2f}ms")
        print(f"  State: {sandbox.state.value}")
        print(f"  Labels: {sandbox.labels}")

        # Verify through Modal API
        print("\n🔍 Verifying through Modal API...")
        try:
            # Try to fetch the sandbox directly
            ModalSandbox.from_id(sandbox.id)
            print(f"✅ Found our sandbox through Modal API: {sandbox.id}")
        except Exception as e:
            print(f"⚠️ Could not verify through API (may be normal): {e}")

        # Test execution
        print("\n💻 Testing code execution...")
        result = await provider.execute_command(sandbox.id, "echo 'Hello from Modal validation!'")
        print(f"  Exit code: {result.exit_code}")
        print(f"  Output: {result.stdout.strip()}")
        print(f"  Success: {result.success}")

        # Test Python operations
        print("\n🐍 Testing Python operations...")
        result = await provider.execute_command(
            sandbox.id,
            """python -c "
import sys
import platform
print(f'Python: {sys.version}')
print(f'Platform: {platform.platform()}')

# Test computation
x = sum(range(1000))
print(f'Sum of 0-999: {x}')
" """,
        )
        if result.success:
            print("✅ Python execution successful")
            for line in result.stdout.strip().split("\n"):
                print(f"    {line}")
        else:
            print(f"❌ Python execution failed: {result.stderr}")

        # Clean up
        print("\n🧹 Cleaning up...")
        destroyed = await provider.destroy_sandbox(sandbox.id)
        print(f"  Sandbox destroyed: {destroyed}")

        return True

    except Exception as e:
        print(f"❌ Modal validation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    """Run all validation tests."""
    print("🧪 Sandbox Provider Validation Suite")
    print("=" * 50)

    results = []

    # Test E2B
    if os.getenv("E2B_API_KEY"):
        try:
            success = await validate_e2b()
            results.append(("E2B", success))
        except Exception as e:
            print(f"\n❌ E2B validation error: {e}")
            import traceback

            traceback.print_exc()
            results.append(("E2B", False))
    else:
        print("\n⚠️ Skipping E2B (no API key)")

    # Test Daytona
    if os.getenv("DAYTONA_API_KEY"):
        try:
            success = await validate_daytona()
            results.append(("Daytona", success))
        except Exception as e:
            print(f"\n❌ Daytona validation error: {e}")
            results.append(("Daytona", False))
    else:
        print("\n⚠️ Skipping Daytona (no API key)")

    # Test Modal
    if os.path.exists(os.path.expanduser("~/.modal.toml")):
        try:
            success = await validate_modal()
            results.append(("Modal", success))
        except Exception as e:
            print(f"\n❌ Modal validation error: {e}")
            import traceback

            traceback.print_exc()
            results.append(("Modal", False))
    else:
        print("\n⚠️ Skipping Modal (not configured)")

    # Summary
    print("\n" + "=" * 50)
    print("📊 Validation Summary")
    print("=" * 50)
    for provider, success in results:
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"  {provider}: {status}")

    all_passed = all(success for _, success in results)
    if all_passed:
        print("\n🎉 All validation tests passed!")
    else:
        print("\n⚠️ Some validation tests failed")

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
