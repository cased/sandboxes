"""Sandbox provider implementations."""

from ..base import SandboxProvider

# Import providers conditionally
_providers = {}

try:
    from .daytona import DaytonaProvider

    _providers["daytona"] = DaytonaProvider
except ImportError:
    pass

try:
    from .e2b import E2BProvider

    _providers["e2b"] = E2BProvider
except ImportError:
    pass

try:
    from .modal import ModalProvider

    _providers["modal"] = ModalProvider
except ImportError:
    pass

try:
    from .cloudflare import CloudflareProvider

    _providers["cloudflare"] = CloudflareProvider
except ImportError:
    pass

try:
    from .hopx import HopxProvider

    _providers["hopx"] = HopxProvider
except ImportError:
    pass

try:
    from .vercel import VercelProvider

    _providers["vercel"] = VercelProvider
except ImportError:
    pass

try:
    from .sprites import SpritesProvider

    _providers["sprites"] = SpritesProvider
except ImportError:
    pass


def get_provider(name: str) -> type[SandboxProvider] | None:
    """Get a provider class by name."""
    return _providers.get(name)


def list_available_providers() -> list[str]:
    """List all available provider names."""
    return list(_providers.keys())


__all__ = ["get_provider", "list_available_providers"]
