"""Best-effort model discovery for operator settings.

Discovery is advisory only. Runtime provider configuration remains authoritative
and custom model names are always accepted by the provider boundary.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


def _safe_endpoint(value: str) -> str:
    """Return an endpoint suitable for the browser, without credentials/query secrets."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host + (f":{parsed.port}" if parsed.port else "")
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
    except ValueError:
        return ""


def available_models(role: str, *, provider: str | None = None, base_url: str | None = None) -> dict[str, object]:
    role = role.strip().lower()
    prefix = "KOGWISTAR_PARSER" if role == "parser" else "KOGWISTAR_MAINTENANCE"
    provider = (provider or os.getenv(f"{prefix}_PROVIDER", "ollama")).strip().lower()
    raw_base_url = base_url or os.getenv(f"{prefix}_BASE_URL", "http://localhost:11434")
    safe_base_url = _safe_endpoint(raw_base_url)
    if not safe_base_url:
        return {"role": role, "provider": provider, "base_url": "", "models": [], "source": "invalid_endpoint"}
    base_url = safe_base_url
    configured = os.getenv(f"{prefix}_MODEL", "")
    models: list[str] = [configured] if configured else []
    source = "configured"
    catalog = os.getenv("LLM_WIKI_MODEL_CATALOG_JSON", "")
    if catalog:
        try:
            payload = json.loads(catalog)
            values = payload.get(role, payload) if isinstance(payload, dict) else payload
            if isinstance(values, list):
                models.extend(str(value) for value in values if str(value).strip())
                source = "configured_catalog"
        except json.JSONDecodeError:
            source = "configured_catalog_invalid"
    else:
        try:
            if provider == "ollama":
                path = "/api/tags"
            else:
                path = "/models" if base_url.rstrip("/").endswith("/v1") else "/v1/models"
            with urlopen(Request(base_url + path, headers={"accept": "application/json"}), timeout=1.5) as response:
                payload = json.loads(response.read(1_000_000).decode("utf-8"))
            entries = payload.get("models", []) if provider == "ollama" else payload.get("data", [])
            models.extend(str(entry.get("name") or entry.get("id")) for entry in entries if isinstance(entry, dict))
            source = "provider"
        except Exception:  # discovery must never block settings or startup
            source = "unavailable"
    deduplicated = sorted({model for model in models if model and model != "None"})
    return {"role": role, "provider": provider, "base_url": base_url, "models": deduplicated, "source": source}
