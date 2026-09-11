from __future__ import annotations

from kogwistar_llm_wiki.model_catalog import available_models


def test_model_catalog_accepts_custom_router_catalog(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_PARSER_PROVIDER", "router")
    monkeypatch.setenv("KOGWISTAR_PARSER_MODEL", "router/custom-model")
    monkeypatch.setenv("LLM_WIKI_MODEL_CATALOG_JSON", '{"parser": ["router/fast", "router/accurate"]}')

    result = available_models("parser")

    assert result["provider"] == "router"
    assert result["source"] == "configured_catalog"
    assert result["models"] == ["router/accurate", "router/custom-model", "router/fast"]


def test_openai_v1_endpoint_does_not_duplicate_v1(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self, _limit):
            return b'{"data": [{"id": "router-model"}]}'

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return Response()

    monkeypatch.delenv("LLM_WIKI_MODEL_CATALOG_JSON", raising=False)
    monkeypatch.setenv("KOGWISTAR_PARSER_PROVIDER", "router")
    monkeypatch.setattr("kogwistar_llm_wiki.model_catalog.urlopen", fake_urlopen)
    result = available_models("parser", provider="router", base_url="https://router.example/v1")
    assert calls == ["https://router.example/v1/models"]
    assert result["models"] == ["router-model"]


def test_malformed_endpoint_is_reported_without_raising() -> None:
    result = available_models("parser", base_url="http://localhost:notaport")
    assert result["source"] == "invalid_endpoint"
    assert result["models"] == []
