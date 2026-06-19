from __future__ import annotations

from dataclasses import replace

import pytest

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import EmbeddingProviderConfig, ProviderEndpointConfig, WorkflowProviderSettings


SMOKE_PARSER_CASES = [
    pytest.param(
        "fake",
        "fake-smoke-parser",
        id="fake",
    ),
    pytest.param(
        "ollama",
        "gemma4:e2b",
        id="ollama-gemma4",
        marks=pytest.mark.manual,
    ),
]


@pytest.mark.parametrize("parser_provider,parser_model", SMOKE_PARSER_CASES)
def test_ingest_pipeline_smoke(pipeline, ingest_request, parser_provider, parser_model):
    request = ingest_request
    if parser_provider == "ollama":
        pytest.importorskip("langchain_ollama")
        base_url = "http://127.0.0.1:11434"
        pytest.importorskip("requests")
        import requests

        try:
            response = requests.get(f"{base_url}/api/version", timeout=1.5)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"local Ollama is not available at {base_url}: {exc}")
        if not response.ok:
            pytest.skip(f"local Ollama is not healthy at {base_url}")
        provider_settings = WorkflowProviderSettings(
            parser=ProviderEndpointConfig(provider="ollama", model=parser_model, base_url=base_url),
            embedding=EmbeddingProviderConfig(provider="fake", model="smoke-embed", dimension=2),
        )
        request = replace(
            ingest_request,
            parser_mode="ollama",
            llm_provider="ollama",
            llm_model=parser_model,
        )
    else:
        provider_settings = WorkflowProviderSettings(
            parser=ProviderEndpointConfig(provider="fake", model=parser_model),
            embedding=EmbeddingProviderConfig(provider="fake", model="smoke-embed", dimension=2),
        )

    def _parser(**kwargs):
        kwargs.pop("llm_provider", None)
        kwargs.pop("model", None)
        kwargs.pop("provider_settings", None)
        return parse_page_index_document(provider_settings=provider_settings, **kwargs)

    pipeline.parser = _parser

    artifacts = pipeline.run(request)
    assert artifacts.source_document_id
    assert artifacts.maintenance_job_id
    assert artifacts.candidate_link_id
    assert artifacts.promotion_candidate_id
    assert artifacts.promoted_entity_id is None
