from __future__ import annotations

from kogwistar_llm_wiki.provider_config import (
    build_provider_endpoint_config,
    normalize_provider_name,
    provider_config_summary,
    resolve_parser_provider_settings,
)


def test_normalize_provider_name_maps_azure_aliases() -> None:
    assert normalize_provider_name("azure_openai") == "azure"
    assert normalize_provider_name("azure") == "azure"
    assert normalize_provider_name("Ollama") == "ollama"


def test_build_provider_endpoint_config_prefers_kogwistar_env_over_legacy_env(monkeypatch) -> None:
    monkeypatch.setenv("KOGWISTAR_PARSER_PROVIDER", "azure_openai")
    monkeypatch.setenv("KOGWISTAR_PARSER_MODEL", "gpt-5-nano")
    monkeypatch.setenv("KOGWISTAR_PARSER_BASE_URL", "https://example.openai.azure.com/")
    monkeypatch.setenv("KOGWISTAR_PARSER_API_KEY_ENV", "OPENAI_API_KEY_GPT5_NANO")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_ENDPOINT_GPT5_NANO", "https://example.openai.azure.com/")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_VERSION_GPT5_NANO", "2024-10-21")
    monkeypatch.setenv("KG_DOC_PARSER_PROVIDER", "ollama")
    monkeypatch.setenv("KG_DOC_PARSER_MODEL", "legacy-model")
    monkeypatch.setenv("KG_DOC_PARSER_BASE_URL", "http://127.0.0.1:11434")

    config = build_provider_endpoint_config("parser")

    assert config.provider == "azure"
    assert config.model == "gpt-5-nano"
    assert config.base_url == "https://example.openai.azure.com/"
    assert config.api_key_env == "OPENAI_API_KEY_GPT5_NANO"
    assert config.api_version == "2024-10-21"


def test_build_provider_endpoint_config_uses_model_specific_api_version(monkeypatch) -> None:
    monkeypatch.setenv("KOGWISTAR_PARSER_PROVIDER", "azure_openai")
    monkeypatch.setenv("KOGWISTAR_PARSER_MODEL", "gpt-4o")
    monkeypatch.setenv("KOGWISTAR_PARSER_BASE_URL", "https://example.openai.azure.com/")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_VERSION_GPT4O", "2024-12-01-preview")

    config = build_provider_endpoint_config("parser")

    assert config.provider == "azure"
    assert config.model == "gpt-4o"
    assert config.api_version == "2024-12-01-preview"


def test_resolve_parser_provider_settings_returns_summary(monkeypatch) -> None:
    monkeypatch.setenv("KOGWISTAR_PARSER_PROVIDER", "ollama")
    monkeypatch.setenv("KOGWISTAR_PARSER_MODEL", "gemma4:e2b")
    monkeypatch.setenv("KOGWISTAR_PARSER_BASE_URL", "http://localhost:11434")

    settings = resolve_parser_provider_settings()
    summary = provider_config_summary(settings)

    assert summary["provider"] == "ollama"
    assert summary["model"] == "gemma4:e2b"
    assert summary["base_url"] == "http://localhost:11434"
