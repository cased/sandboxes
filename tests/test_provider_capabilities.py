"""Tests for provider capability declarations."""

from sandboxes.providers.cloudflare import CloudflareProvider
from sandboxes.providers.daytona import DaytonaProvider
from sandboxes.providers.e2b import E2BProvider
from sandboxes.providers.hopx import HopxProvider
from sandboxes.providers.modal import ModalProvider
from sandboxes.providers.sprites import SpritesProvider
from sandboxes.providers.vercel import VercelProvider


def test_provider_capability_matrix_contract():
    """Providers should declare capabilities that match implemented features."""
    expected = {
        "e2b": {
            "persistent": True,
            "snapshot": False,
            "streaming": True,
            "file_upload": True,
            "interactive_shell": False,
            "gpu": False,
        },
        "modal": {
            "persistent": True,
            "snapshot": False,
            "streaming": True,
            "file_upload": False,
            "interactive_shell": False,
            "gpu": False,
        },
        "daytona": {
            "persistent": True,
            "snapshot": True,
            "streaming": False,
            "file_upload": True,
            "interactive_shell": False,
            "gpu": False,
        },
        "hopx": {
            "persistent": True,
            "snapshot": False,
            "streaming": True,
            "file_upload": True,
            "interactive_shell": False,
            "gpu": False,
        },
        "sprites": {
            "persistent": True,
            "snapshot": False,
            "streaming": True,
            "file_upload": False,
            "interactive_shell": True,
            "gpu": False,
        },
        "cloudflare": {
            "persistent": True,
            "snapshot": False,
            "streaming": True,
            "file_upload": True,
            "interactive_shell": False,
            "gpu": False,
        },
        "vercel": {
            "persistent": True,
            "snapshot": True,
            "streaming": True,
            "file_upload": True,
            "interactive_shell": True,
            "gpu": False,
        },
    }

    observed = {
        "e2b": E2BProvider.get_capabilities().as_dict(),
        "modal": ModalProvider.get_capabilities().as_dict(),
        "daytona": DaytonaProvider.get_capabilities().as_dict(),
        "hopx": HopxProvider.get_capabilities().as_dict(),
        "sprites": SpritesProvider.get_capabilities().as_dict(),
        "cloudflare": CloudflareProvider.get_capabilities().as_dict(),
        "vercel": VercelProvider.get_capabilities().as_dict(),
    }

    assert observed == expected
