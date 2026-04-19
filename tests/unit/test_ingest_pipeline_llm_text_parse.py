from __future__ import annotations



from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest
from types import SimpleNamespace
import pytest

class _FakeParseResult:
    def __init__(self, title: str):
        self.semantic_tree = SimpleNamespace(title=title)


@pytest.mark.parametrize(
    ("parser_mode", "llm_provider", "llm_model"),
    [
        pytest.param("ollama", "ollama", "gemma3:4b", id="ollama"),
        pytest.param("gemini", "gemini", "gemini-2.5-flash", id="gemini"),
    ],
)
def test_parse_source_passes_llm_text_parse_args(pipeline, ingest_request, parser_mode, llm_provider, llm_model):
    called: dict[str, object] = {}

    def fake_parser(**kwargs):
        called.update(kwargs)
        return _FakeParseResult(title=kwargs["title"])

    pipeline.parser = fake_parser
    ingest_request = ingest_request.model_copy(
        update={
            "parser_mode": parser_mode,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
        }
    )

    result = pipeline.parse_source(request=ingest_request, source_document_id="doc-1")

    assert result.semantic_tree.title == ingest_request.title
    assert called["document_id"] == "doc-1"
    assert called["mode"] == parser_mode
    assert called["llm_provider"] == llm_provider
    assert called["model"] == llm_model
    
def test_parse_source_supports_kwargs_only_parser(namespace_engines):
    captured = {}

    def kwargs_only_parser(**kwargs):
        captured.update(kwargs)

        class _SemanticTree:
            title = "Acme Contract"

        class _Result:
            semantic_tree = _SemanticTree()

        return _Result()

    pipeline = IngestPipeline(namespace_engines, parser=kwargs_only_parser)
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days.",
        parser_mode="ollama",
        llm_provider="ollama",
        llm_model="gemma3:4b",
    )

    pipeline.parse_source(request=request, source_document_id="doc-1")

    assert captured["mode"] == "ollama"
    assert captured["llm_provider"] == "ollama"
    assert captured["model"] == "gemma3:4b"
    assert captured["provider_settings"].parser.provider == "ollama"
    assert captured["provider_settings"].parser.model == "gemma3:4b"

def test_parse_source_prefers_explicit_llm_provider_and_model(namespace_engines):
    captured = {}

    def fake_parser(**kwargs):
        captured.update(kwargs)

        class _SemanticTree:
            title = "Acme Contract"

        class _Result:
            semantic_tree = _SemanticTree()

        return _Result()

    pipeline = IngestPipeline(namespace_engines, parser=fake_parser)
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days.",
        parser_mode="ollama",
        llm_provider="ollama",
        llm_model="gemma3:4b",
    )

    pipeline.parse_source(request=request, source_document_id="doc-1")

    assert captured["mode"] == "ollama"
    assert captured["llm_provider"] == "ollama"
    assert captured["model"] == "gemma3:4b"


def test_parse_source_falls_back_to_provider_settings_for_old_parser_api(namespace_engines):
    captured = {}

    def old_style_parser(*, provider_settings=None, **kwargs):
        captured["provider_settings"] = provider_settings
        captured.update(kwargs)

        class _SemanticTree:
            title = "Acme Contract"

        class _Result:
            semantic_tree = _SemanticTree()

        return _Result()

    pipeline = IngestPipeline(namespace_engines, parser=old_style_parser)
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days.",
        parser_mode="gemini",
        llm_provider="gemini",
        llm_model="gemini-2.5-flash",
    )

    pipeline.parse_source(request=request, source_document_id="doc-1")

    assert captured["mode"] == "gemini"
    assert captured["provider_settings"].parser.provider == "gemini"
    assert captured["provider_settings"].parser.model == "gemini-2.5-flash"
