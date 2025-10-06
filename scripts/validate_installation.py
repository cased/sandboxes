#!/usr/bin/env python
"""Validate that sandbox providers are correctly installed and configured.

This script checks:
1. Required packages are installed
2. API keys/authentication is configured
3. Basic sandbox operations work
4. Network connectivity to provider services

Usage:
    python scripts/validate_installation.py
"""

import asyncio
import importlib.util
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check_package(package_name: str) -> bool:
    """Check if a package is installed."""
    spec = importlib.util.find_spec(package_name)
    return spec is not None


def check_environment():
    """Check environment setup."""
    print("🔍 ENVIRONMENT CHECK")
    print("=" * 60)

    results = []

    # Check Python version
    import platform

    py_version = platform.python_version()
    py_major, py_minor = map(int, py_version.split(".")[:2])
    if py_major >= 3 and py_minor >= 9:
        print(f"✅ Python {py_version}")
        results.append(True)
    else:
        print(f"❌ Python {py_version} (requires 3.9+)")
        results.append(False)

    # Check core packages
    core_packages = [
        ("click", "CLI framework"),
        ("tabulate", "Table formatting"),
        ("typing_extensions", "Type hints"),
    ]

    print("\nCore packages:")
    for pkg, desc in core_packages:
        if check_package(pkg):
            print(f"  ✅ {pkg:20} - {desc}")
            results.append(True)
        else:
            print(f"  ❌ {pkg:20} - {desc} (install with: uv pip install {pkg})")
            results.append(False)

    # Check provider packages
    provider_packages = [
        ("modal", "Modal provider", "uv pip install modal"),
        ("e2b", "E2B provider", "uv pip install e2b"),
        ("daytona", "Daytona provider", "uv pip install daytona"),
    ]

    print("\nProvider packages:")
    for pkg, desc, install_cmd in provider_packages:
        if check_package(pkg):
            print(f"  ✅ {pkg:20} - {desc}")
            results.append(True)
        else:
            print(f"  ⚠️  {pkg:20} - {desc} (optional, install with: {install_cmd})")
            # Don't count optional packages as failures

    return all(results)


def check_authentication():
    """Check provider authentication."""
    print("\n🔑 AUTHENTICATION CHECK")
    print("=" * 60)

    auth_status = []

    # Check Modal
    modal_config = os.path.expanduser("~/.modal.toml")
    if os.path.exists(modal_config):
        print("✅ Modal: Configured (~/.modal.toml found)")
        auth_status.append(("modal", True))
    else:
        print("❌ Modal: Not configured (run: modal token set)")
        auth_status.append(("modal", False))

    # Check E2B
    if os.getenv("E2B_API_KEY"):
        print("✅ E2B: API key set")
        auth_status.append(("e2b", True))
    else:
        print("⚠️  E2B: No API key (set E2B_API_KEY environment variable)")
        auth_status.append(("e2b", False))

    # Check Daytona
    if os.getenv("DAYTONA_API_KEY"):
        print("✅ Daytona: API key set")
        auth_status.append(("daytona", True))
    else:
        print("⚠️  Daytona: No API key (set DAYTONA_API_KEY environment variable)")
        auth_status.append(("daytona", False))

    configured = [name for name, status in auth_status if status]
    if configured:
        print(f"\nConfigured providers: {', '.join(configured)}")
    else:
        print("\n⚠️  No providers configured")

    return auth_status


async def test_provider_operations():
    """Test basic operations for configured providers."""
    print("\n🧪 PROVIDER OPERATIONS TEST")
    print("=" * 60)

    from sandboxes import SandboxConfig

    results = []

    # Test Modal if available
    try:
        from sandboxes.providers.modal import ModalProvider

        print("\nTesting Modal...")
        provider = ModalProvider()
        config = SandboxConfig(image="python:3.11-slim", labels={"validation": "true"})

        sandbox = await provider.create_sandbox(config)
        print(f"  ✅ Created sandbox: {sandbox.id[:20]}...")

        result = await provider.execute_command(sandbox.id, "echo 'validation test'")
        if result.success and "validation test" in result.stdout:
            print("  ✅ Command execution works")
        else:
            print("  ❌ Command execution failed")

        await provider.destroy_sandbox(sandbox.id)
        print("  ✅ Sandbox destroyed")

        results.append(("Modal", True))

    except Exception as e:
        print(f"  ❌ Modal test failed: {e}")
        results.append(("Modal", False))

    # Test E2B if available
    if os.getenv("E2B_API_KEY"):
        try:
            from sandboxes.providers.e2b import E2BProvider

            print("\nTesting E2B...")
            provider = E2BProvider()
            config = SandboxConfig(labels={"validation": "true"})

            sandbox = await provider.create_sandbox(config)
            print(f"  ✅ Created sandbox: {sandbox.id[:20]}...")

            result = await provider.execute_command(sandbox.id, "echo 'validation test'")
            if result.success and "validation test" in result.stdout:
                print("  ✅ Command execution works")
            else:
                print("  ❌ Command execution failed")

            await provider.destroy_sandbox(sandbox.id)
            print("  ✅ Sandbox destroyed")

            results.append(("E2B", True))

        except Exception as e:
            print(f"  ❌ E2B test failed: {e}")
            results.append(("E2B", False))

    # Test Daytona if available
    if os.getenv("DAYTONA_API_KEY"):
        try:
            from sandboxes.providers.daytona import DaytonaProvider

            print("\nTesting Daytona...")
            provider = DaytonaProvider()
            config = SandboxConfig(labels={"validation": "true"})

            sandbox = await provider.create_sandbox(config)
            print(f"  ✅ Created sandbox: {sandbox.id[:20]}...")

            result = await provider.execute_command(sandbox.id, "echo 'validation test'")
            if result.success and "validation test" in result.stdout:
                print("  ✅ Command execution works")
            else:
                print("  ❌ Command execution failed")

            await provider.destroy_sandbox(sandbox.id)
            print("  ✅ Sandbox destroyed")

            results.append(("Daytona", True))

        except Exception as e:
            print(f"  ❌ Daytona test failed: {e}")
            results.append(("Daytona", False))

    return results


async def main():
    """Run all validation checks."""
    print("🏥 CASED-SANDBOXES INSTALLATION VALIDATOR")
    print("=" * 60)

    # Check environment
    env_ok = check_environment()

    # Check authentication
    auth_status = check_authentication()

    # Test operations if any provider is configured
    configured_providers = [name for name, status in auth_status if status]

    operation_results = []
    if configured_providers:
        operation_results = await test_provider_operations()

    # Final summary
    print("\n" + "=" * 60)
    print("📊 VALIDATION SUMMARY")
    print("=" * 60)

    all_good = True

    if env_ok:
        print("✅ Environment: Ready")
    else:
        print("❌ Environment: Issues found")
        all_good = False

    auth_count = sum(1 for _, status in auth_status if status)
    if auth_count > 0:
        print(f"✅ Authentication: {auth_count} provider(s) configured")
    else:
        print("⚠️  Authentication: No providers configured")

    if operation_results:
        working = sum(1 for _, status in operation_results if status)
        if working > 0:
            print(f"✅ Operations: {working}/{len(operation_results)} provider(s) working")
        else:
            print("❌ Operations: No providers working")
            all_good = False

    print("\n" + "=" * 60)
    if all_good and auth_count > 0:
        print("✅ VALIDATION PASSED - System is ready!")
    elif auth_count == 0:
        print("⚠️  VALIDATION WARNING - No providers configured")
        print("\nNext steps:")
        print("1. Configure at least one provider (see Authentication section above)")
        print("2. Run this script again to verify")
    else:
        print("❌ VALIDATION FAILED - Please fix issues above")

    return 0 if all_good else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
