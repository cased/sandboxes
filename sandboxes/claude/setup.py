"""Provider-specific Claude Code setup logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .exceptions import ClaudeCodeNotInstalledError, ClaudeCodeSetupError

if TYPE_CHECKING:
    from ..sandbox import Sandbox

logger = logging.getLogger(__name__)

# Prebaked image identifiers for providers that support them
PREBAKED_IMAGES = {
    "e2b": "anthropic-claude-code",  # E2B's official template
    "modal": "claude-code-image",  # Use get_modal_claude_image() to build
}


def get_modal_claude_image() -> Any:
    """Get a Modal Image with Claude Code pre-installed.

    Returns a Modal Image that can be used when creating sandboxes.
    The image is cached by Modal after first build.

    Returns:
        modal.Image with Node.js and Claude Code installed

    Example:
        >>> image = get_modal_claude_image()
        >>> sandbox = modal.Sandbox.create(app=app, image=image)
    """
    try:
        import modal
    except ImportError as e:
        raise ImportError("Modal SDK required: pip install modal") from e

    return (
        modal.Image.debian_slim()
        .apt_install("curl", "ca-certificates")
        .run_commands(
            "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
            "apt-get install -y nodejs",
            "npm install -g @anthropic-ai/claude-code",
        )
    )


# Provider-specific configuration for Claude Code setup
PROVIDER_CONFIG = {
    "e2b": {
        # E2B has anthropic-claude-code template with everything pre-installed
        # but template may be outdated - run 'claude update' to get latest
        "template": "anthropic-claude-code",
        "setup_commands": ["sudo claude update"],  # Update to latest version
        "has_nodejs": True,
        "check_command": "claude --version",
        "prebaked": True,
    },
    "modal": {
        # Modal can use a prebaked image - call get_modal_claude_image()
        "template": None,
        "setup_commands": [],  # No runtime setup needed with prebaked image
        "has_nodejs": True,
        "check_command": "claude --version",
        "prebaked": True,
        "image_builder": "get_modal_claude_image",
    },
    "daytona": {
        "template": None,
        "setup_commands": [
            # Daytona images typically have Node.js
            "npm install -g @anthropic-ai/claude-code",
        ],
        "has_nodejs": True,
        "check_command": "claude --version",
    },
    "hopx": {
        "template": None,
        "setup_commands": [
            "npm install -g @anthropic-ai/claude-code",
        ],
        "has_nodejs": True,
        "check_command": "claude --version",
    },
    "cloudflare": {
        "template": None,
        "setup_commands": [
            "npm install -g @anthropic-ai/claude-code",
        ],
        "has_nodejs": True,
        "check_command": "claude --version",
    },
    "sprites": {
        # Sprites come with Claude Code, Node.js 22, Python 3.13 pre-installed
        # Fast startup (1-2s), 100GB storage, checkpoint/restore support
        # See: https://simonwillison.net/2026/Jan/9/sprites-dev/
        "template": None,
        "setup_commands": ["claude update"],  # Just update to latest version
        "has_nodejs": True,
        "check_command": "claude --version",
        "prebaked": True,  # Claude Code is pre-installed
    },
}

# Default setup for unknown providers
DEFAULT_PROVIDER_CONFIG = {
    "template": None,
    "setup_commands": [
        # Try to install Node.js if not present
        "which node || (curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && apt-get install -y nodejs)",
        "npm install -g @anthropic-ai/claude-code",
    ],
    "has_nodejs": False,
    "check_command": "claude --version",
}


def get_provider_config(provider_name: str) -> dict:
    """Get configuration for a specific provider.

    Args:
        provider_name: Name of the sandbox provider

    Returns:
        Provider configuration dict
    """
    return PROVIDER_CONFIG.get(provider_name.lower(), DEFAULT_PROVIDER_CONFIG)


def get_recommended_template(provider_name: str) -> str | None:
    """Get the recommended template/image for Claude Code on a provider.

    Args:
        provider_name: Name of the sandbox provider

    Returns:
        Template name or None if default should be used
    """
    config = get_provider_config(provider_name)
    return config.get("template")


def get_setup_commands(provider_name: str) -> list[str]:
    """Get setup commands for installing Claude Code on a provider.

    Args:
        provider_name: Name of the sandbox provider

    Returns:
        List of shell commands to run
    """
    config = get_provider_config(provider_name)
    return config.get("setup_commands", [])


async def check_claude_code_installed(sandbox: Sandbox) -> bool:
    """Check if Claude Code CLI is installed in the sandbox.

    Args:
        sandbox: The sandbox to check

    Returns:
        True if Claude Code is installed and working
    """
    try:
        result = await sandbox.execute("claude --version")
        return result.success and "claude" in result.stdout.lower()
    except Exception:
        return False


async def check_nodejs_installed(sandbox: Sandbox) -> bool:
    """Check if Node.js is installed in the sandbox.

    Args:
        sandbox: The sandbox to check

    Returns:
        True if Node.js is installed
    """
    try:
        result = await sandbox.execute("node --version")
        if result.success:
            # Check version is 18+
            version_str = result.stdout.strip()
            if version_str.startswith("v"):
                major = int(version_str[1:].split(".")[0])
                return major >= 18
        return False
    except Exception:
        return False


async def setup_claude_code(
    sandbox: Sandbox,
    provider_name: str | None = None,
    skip_if_installed: bool = True,
    anthropic_api_key: str | None = None,
) -> bool:
    """Set up Claude Code in a sandbox.

    This function handles the full setup process:
    1. Checks if Claude Code is already installed (optional skip)
    2. Runs provider-specific setup commands (always runs for updates)
    3. Verifies installation succeeded
    4. Sets up environment variables

    Args:
        sandbox: The sandbox to set up
        provider_name: Name of the provider (auto-detect from sandbox if None)
        skip_if_installed: Skip setup if already installed
        anthropic_api_key: API key to set (optional, can be set later)

    Returns:
        True if setup succeeded

    Raises:
        ClaudeCodeSetupError: If setup fails
    """
    # Auto-detect provider if not specified
    if provider_name is None:
        provider_name = getattr(sandbox, "provider", "unknown")

    logger.info(f"Setting up Claude Code in {provider_name} sandbox {sandbox.id}")

    # Get setup commands for this provider
    setup_commands = get_setup_commands(provider_name)
    already_installed = await check_claude_code_installed(sandbox)

    # Skip only if installed AND no setup commands to run
    # (we always want to run setup commands as they may include updates)
    if skip_if_installed and already_installed and not setup_commands:
        logger.info("Claude Code already installed, skipping setup")
        return True

    if not setup_commands and not already_installed:
        raise ClaudeCodeNotInstalledError(
            f"Claude Code not installed and no setup commands for provider {provider_name}"
        )

    # Run setup commands (may be updates for pre-installed templates)
    for cmd in setup_commands:
        logger.debug(f"Running setup command: {cmd}")
        try:
            result = await sandbox.execute(cmd, timeout=300)  # 5 min timeout for installs
            if not result.success:
                logger.warning(f"Setup command failed: {cmd}\nstderr: {result.stderr}")
                # Continue with other commands, some may be optional
        except Exception as e:
            logger.error(f"Setup command error: {cmd}\n{e}")
            raise ClaudeCodeSetupError(f"Failed to run setup command: {cmd}") from e

    # Verify installation
    if not await check_claude_code_installed(sandbox):
        raise ClaudeCodeNotInstalledError(
            "Claude Code installation failed - CLI not available after setup"
        )

    logger.info("Claude Code setup completed successfully")
    return True


async def setup_environment(
    sandbox: Sandbox,
    anthropic_api_key: str | None = None,
    additional_env_vars: dict[str, str] | None = None,
) -> bool:
    """Set up environment variables for Claude Code.

    Args:
        sandbox: The sandbox to configure
        anthropic_api_key: Anthropic API key
        additional_env_vars: Additional environment variables

    Returns:
        True if setup succeeded
    """
    env_vars = additional_env_vars or {}

    if anthropic_api_key:
        env_vars["ANTHROPIC_API_KEY"] = anthropic_api_key

    if not env_vars:
        return True

    # Export environment variables
    for key, value in env_vars.items():
        # Escape single quotes in value
        escaped_value = value.replace("'", "'\\''")
        result = await sandbox.execute(f"export {key}='{escaped_value}'")
        if not result.success:
            logger.warning(f"Failed to set environment variable {key}")

    return True
