from __future__ import annotations

import pytest
from kg_doc_parser.workflow_ingest.providers import (
    ProviderEndpointConfig,
    WorkflowProviderSettings,
)

from kogwistar_llm_wiki.maintenance.worker_parse import (
    _build_maintenance_parse_request,
)


@pytest.mark.parametrize("provider", ["openai", "codex", "ollama"])
def test_maintenance_parse_uses_configured_provider_not_source_heuristic(provider: str) -> None:
    settings = WorkflowProviderSettings(
        parser=ProviderEndpointConfig(
            provider=provider,
            model="bonsai-test-model",
            base_url="http://model.example/v1",
            api_key_env="MAINTENANCE_MODEL_KEY",
        )
    )

    request, selected_settings = _build_maintenance_parse_request(
        workspace_id="stocks-smoke",
        source_document_id="source-1",
        raw_text="Pinned source text",
        metadata={
            "source_uri": "https://example.test/article",
            "title": "Article",
            "parser_mode": "heuristic",
            "parser_lane": "page_index",
        },
        provider_settings=settings,
    )

    assert request.parser_lane == "workflow_layered"
    assert request.parser_mode == "llm"
    assert request.llm_provider == provider
    assert request.llm_model == "bonsai-test-model"
    assert request.raw_text == "Pinned source text"
    assert selected_settings is settings
    assert selected_settings.parser.base_url == "http://model.example/v1"


def test_maintenance_parse_without_provider_settings_preserves_source_parser_mode() -> None:
    request, selected_settings = _build_maintenance_parse_request(
        workspace_id="stocks-smoke",
        source_document_id="source-1",
        raw_text="Pinned source text",
        metadata={"parser_mode": "heuristic", "parser_lane": "page_index"},
        provider_settings=None,
    )

    assert request.parser_lane == "page_index"
    assert request.parser_mode == "heuristic"
    assert selected_settings is None
