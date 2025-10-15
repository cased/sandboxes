"""Constants and validation for sandboxes library."""

from .exceptions import ProviderError

# Valid provider names
VALID_PROVIDERS = frozenset(["e2b", "modal", "daytona", "cloudflare"])


def validate_provider(provider: str | None, allow_none: bool = True) -> None:
    """
    Validate a provider name.

    Args:
        provider: Provider name to validate
        allow_none: Whether to allow None as a valid value

    Raises:
        ProviderError: If provider is invalid
    """
    if provider is None:
        if allow_none:
            return
        raise ProviderError("Provider cannot be None")

    if provider not in VALID_PROVIDERS:
        raise ProviderError(
            f"Invalid provider: '{provider}'. "
            f"Valid providers are: {', '.join(sorted(VALID_PROVIDERS))}"
        )


def validate_providers(providers: list[str] | None, allow_none: bool = True) -> None:
    """
    Validate a list of provider names.

    Args:
        providers: List of provider names to validate
        allow_none: Whether to allow None values in the list

    Raises:
        ProviderError: If any provider is invalid
    """
    if providers is None:
        return

    for provider in providers:
        validate_provider(provider, allow_none=allow_none)
