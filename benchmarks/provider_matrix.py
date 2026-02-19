"""Shared provider discovery and runtime hints for benchmark scripts."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

STANDARD_IMAGE = os.getenv("BENCHMARK_STANDARD_IMAGE", "daytonaio/ai-test:0.2.3")
_E2B_TEMPLATE_ID_RE = re.compile(r'^template_id\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


@dataclass(frozen=True)
class BenchmarkProvider:
    """Benchmark provider metadata used by benchmark scripts."""

    name: str
    display_name: str
    is_configured: Callable[[], bool]
    load_class: Callable[[], type]
    supports_image_benchmark: bool = True


def _vercel_token() -> str | None:
    return (
        os.getenv("VERCEL_TOKEN")
        or os.getenv("VERCEL_API_TOKEN")
        or os.getenv("VERCEL_ACCESS_TOKEN")
        or os.getenv("VERCEL_OIDC_TOKEN")
    )


def _has_daytona() -> bool:
    return bool(os.getenv("DAYTONA_API_KEY"))


def _has_e2b() -> bool:
    return bool(os.getenv("E2B_API_KEY"))


def _has_sprites() -> bool:
    return bool(os.getenv("SPRITES_TOKEN") or shutil.which("sprite"))


def _has_hopx() -> bool:
    return bool(os.getenv("HOPX_API_KEY"))


def _has_vercel() -> bool:
    return bool(_vercel_token() and os.getenv("VERCEL_PROJECT_ID") and os.getenv("VERCEL_TEAM_ID"))


def _has_modal() -> bool:
    return bool(os.getenv("MODAL_TOKEN_ID") or Path.home().joinpath(".modal.toml").exists())


def _has_cloudflare() -> bool:
    return bool(os.getenv("CLOUDFLARE_SANDBOX_BASE_URL") and os.getenv("CLOUDFLARE_API_TOKEN"))


def _load_daytona_provider():
    from sandboxes.providers.daytona import DaytonaProvider

    return DaytonaProvider


def _load_e2b_provider():
    from sandboxes.providers.e2b import E2BProvider

    return E2BProvider


def _load_sprites_provider():
    from sandboxes.providers.sprites import SpritesProvider

    return SpritesProvider


def _load_hopx_provider():
    from sandboxes.providers.hopx import HopxProvider

    return HopxProvider


def _load_vercel_provider():
    from sandboxes.providers.vercel import VercelProvider

    return VercelProvider


def _load_modal_provider():
    from sandboxes.providers.modal import ModalProvider

    return ModalProvider


def _load_cloudflare_provider():
    from sandboxes.providers.cloudflare import CloudflareProvider

    return CloudflareProvider


PROVIDERS: tuple[BenchmarkProvider, ...] = (
    BenchmarkProvider("daytona", "Daytona", _has_daytona, _load_daytona_provider),
    BenchmarkProvider("e2b", "E2B", _has_e2b, _load_e2b_provider),
    BenchmarkProvider(
        "sprites",
        "Sprites",
        _has_sprites,
        _load_sprites_provider,
        supports_image_benchmark=False,
    ),
    BenchmarkProvider("hopx", "Hopx", _has_hopx, _load_hopx_provider),
    BenchmarkProvider(
        "vercel",
        "Vercel",
        _has_vercel,
        _load_vercel_provider,
        supports_image_benchmark=False,
    ),
    BenchmarkProvider("modal", "Modal", _has_modal, _load_modal_provider),
    BenchmarkProvider(
        "cloudflare",
        "Cloudflare",
        _has_cloudflare,
        _load_cloudflare_provider,
        supports_image_benchmark=False,
    ),
)

PROVIDER_CONFIGURATION_HINTS: dict[str, str] = {
    "daytona": "DAYTONA_API_KEY",
    "e2b": "E2B_API_KEY",
    "sprites": "SPRITES_TOKEN or sprite CLI login",
    "hopx": "HOPX_API_KEY",
    "vercel": "VERCEL_TOKEN + VERCEL_PROJECT_ID + VERCEL_TEAM_ID",
    "modal": "~/.modal.toml or MODAL_TOKEN_ID",
    "cloudflare": "CLOUDFLARE_SANDBOX_BASE_URL + CLOUDFLARE_API_TOKEN",
}


def e2b_benchmark_template() -> str:
    """Return E2B template used for benchmark workloads."""
    configured = os.getenv("E2B_BENCHMARK_TEMPLATE")
    if configured:
        return configured

    # Prefer repository template when available to keep benchmark runtime stable.
    e2b_toml = Path(__file__).parent / "e2b-daytona-benchmark" / "e2b.toml"
    try:
        contents = e2b_toml.read_text()
        match = _E2B_TEMPLATE_ID_RE.search(contents)
        if match:
            return match.group(1)
    except OSError:
        pass

    # Fallback for environments without repository template metadata.
    return "code-interpreter"


def hopx_benchmark_template() -> str:
    """Return Hopx template used for benchmark workloads."""
    return os.getenv("HOPX_BENCHMARK_TEMPLATE", "code-interpreter")


def benchmark_image_for_provider(provider_name: str) -> str | None:
    """Return benchmark image/template hint for a provider."""
    normalized = provider_name.lower()
    if normalized in {"modal", "daytona"}:
        return STANDARD_IMAGE
    if normalized == "e2b":
        return e2b_benchmark_template()
    if normalized == "hopx":
        return hopx_benchmark_template()
    return None


def benchmark_runtime_label(provider_name: str) -> str:
    """Return a human-readable runtime label used in benchmark output."""
    runtime = benchmark_image_for_provider(provider_name)
    if runtime is None:
        return "provider-default runtime"
    if provider_name.lower() in {"e2b", "hopx"}:
        return f"template={runtime}"
    return f"image={runtime}"


def discover_benchmark_providers(
    *,
    include_cloudflare: bool = False,
    image_only: bool = False,
) -> list[BenchmarkProvider]:
    """Return configured providers for benchmark runs."""
    discovered: list[BenchmarkProvider] = []
    for provider in PROVIDERS:
        if provider.name == "cloudflare" and not include_cloudflare:
            continue
        if image_only and not provider.supports_image_benchmark:
            continue
        if provider.is_configured():
            discovered.append(provider)
    return discovered


def discover_provider_names(
    *,
    include_cloudflare: bool = False,
    image_only: bool = False,
) -> list[str]:
    """Return configured benchmark provider names."""
    return [
        provider.name
        for provider in discover_benchmark_providers(
            include_cloudflare=include_cloudflare,
            image_only=image_only,
        )
    ]


def provider_configuration_hints(*, include_cloudflare: bool = False) -> list[str]:
    """Return provider auth hints for benchmark setup messaging."""
    hints = []
    for provider in PROVIDERS:
        if provider.name == "cloudflare" and not include_cloudflare:
            continue
        hints.append(f"- {provider.display_name}: {PROVIDER_CONFIGURATION_HINTS[provider.name]}")
    return hints
