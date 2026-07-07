from __future__ import annotations

import pytest

from tests.smoke.test_llm_wiki_real_azure_smoke import _resolve_real_azure_settings


def test_resolve_real_azure_settings_skips_when_api_key_env_missing(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_PARSER_PROVIDER", "azure_openai")
    monkeypatch.setenv("KOGWISTAR_PARSER_MODEL", "gpt-5-mini")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_ENDPOINT_GPT5_MINI", "https://example.openai.azure.com/")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_VERSION_GPT5_MINI", "2025-01-01-preview")
    monkeypatch.delenv("OPENAI_API_KEY_GPT5_MINI", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("KOGWISTAR_PARSER_API_KEY_ENV", raising=False)
    monkeypatch.delenv("KG_DOC_PARSER_API_KEY_ENV", raising=False)

    with pytest.raises(pytest.skip.Exception, match="missing Azure OpenAI API key env"):
        _resolve_real_azure_settings("gpt-5-mini")


def test_resolve_real_azure_settings_returns_when_env_is_complete(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_PARSER_PROVIDER", "azure_openai")
    monkeypatch.setenv("KOGWISTAR_PARSER_MODEL", "gpt-5-chat")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_ENDPOINT_GPT5_CHAT", "https://chat.example.openai.azure.com/")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_VERSION_GPT5_CHAT", "2025-01-01-preview")
    monkeypatch.setenv("OPENAI_API_KEY_GPT5_CHAT", "chat-key")

    settings = _resolve_real_azure_settings("gpt-5-chat")

    assert settings.parser.provider == "azure"
    assert settings.parser.model == "gpt-5-chat"
    assert settings.parser.base_url == "https://chat.example.openai.azure.com/"
    assert settings.parser.api_key_env == "OPENAI_API_KEY_GPT5_CHAT"
