"""Backward-compatible facade for provider model discovery."""

from urllib.request import Request, urlopen

from .providers.model_catalog import _safe_endpoint
from .providers.model_catalog import available_models as _available_models


def available_models(
    role: str,
    *,
    provider: str | None = None,
    base_url: str | None = None,
) -> dict[str, object]:
    """Discover provider models while retaining the historical URL seam."""

    return _available_models(
        role,
        provider=provider,
        base_url=base_url,
        opener=urlopen,
    )


__all__ = ["Request", "_safe_endpoint", "available_models", "urlopen"]
