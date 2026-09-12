from __future__ import annotations

from pathlib import Path


def test_env_example_is_placeholder_only() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")
    assert "your-openai-api-key" in text
    assert "your-azure-openai-api-key" in text
    assert "metason-model-foundry" not in text
    assert "Qx7Bz2Lm" not in text
    assert "GBmXQzFP" not in text
    assert "AIzaSy" not in text


def test_env_example_documents_supported_auth_and_otel_endpoint_precedence() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "LLM_WIKI_AUTH_MODE=disabled" in text
    assert "LLM_WIKI_AUTH_MODE=personal" not in text
    assert "OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318" in text
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces" in text
