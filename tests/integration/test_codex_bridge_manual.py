"""Opt-in smoke test for a real, host-signed-in Codex bridge."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

pytestmark = [pytest.mark.manual, pytest.mark.slow]


def _json_request(
    url: str,
    *,
    payload: dict[str, object] | None = None,
    token: str | None = None,
    timeout_seconds: float = 10,
) -> dict[str, object]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        pytest.fail(f"Codex bridge returned HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        pytest.fail(f"Codex bridge is not reachable: {exc}")
    assert isinstance(result, dict)
    return result


def test_real_codex_bridge_structured_maintenance_smoke() -> None:
    """Verify one real structured request reaches the user's signed-in Codex session."""
    if os.getenv("KOGWISTAR_RUN_REAL_CODEX") != "1":
        pytest.skip("set KOGWISTAR_RUN_REAL_CODEX=1 to run the real Codex smoke test")
    token = os.getenv("LLM_WIKI_CODEX_BRIDGE_TOKEN")
    if not token:
        pytest.skip("LLM_WIKI_CODEX_BRIDGE_TOKEN is required for the real Codex smoke test")

    base_url = os.getenv("KOGWISTAR_MANUAL_CODEX_BRIDGE_URL", "http://127.0.0.1:8791").rstrip("/")
    health = _json_request(f"{base_url}/healthz")
    assert health.get("ok") is True
    assert health.get("provider") == "codex"

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "statement": {"type": "string", "minLength": 1, "maxLength": 500},
            "confidence": {"type": "string", "enum": ["verified", "inferred"]},
        },
        "required": ["statement", "confidence"],
    }
    request_payload: dict[str, object] = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Return one concise project-memory candidate. The project uses "
                    "append-only maintenance records. Mark this statement as inferred."
                ),
            }
        ],
        "response_schema": schema,
    }
    model = os.getenv("KOGWISTAR_MAINTENANCE_CODEX_MODEL")
    if model:
        request_payload["model"] = model
    result = _json_request(
        f"{base_url}/v1/structured",
        token=token,
        payload=request_payload,
        timeout_seconds=float(os.getenv("KOGWISTAR_MAINTENANCE_CODEX_TIMEOUT_SECONDS", "300")) + 10,
    )
    assert result.get("provider") == "codex"
    output = result.get("output")
    assert isinstance(output, dict)
    assert isinstance(output.get("statement"), str) and output["statement"]
    assert output.get("confidence") == "inferred"
