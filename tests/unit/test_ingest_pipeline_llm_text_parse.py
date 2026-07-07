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


def test_parse_source_passes_trace_log_when_debug_run_dir_is_enabled(namespace_engines, tmp_path):
    captured = {}

    def fake_parser(**kwargs):
        captured.update(kwargs)
        trace_log = kwargs["trace_log"]
        trace_log("page_index_trace_line")

        class _SemanticTree:
            title = "Acme Contract"

        class _Result:
            semantic_tree = _SemanticTree()

        return _Result()

    pipeline = IngestPipeline(namespace_engines, parser=fake_parser, debug_run_dir=tmp_path / "debug")
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days.",
        parser_mode="ollama",
        llm_provider="ollama",
        llm_model="gemma3:4b",
    )

    result = pipeline.parse_source(request=request, source_document_id="doc-1")

    assert result.semantic_tree.title == "Acme Contract"
    assert callable(captured["trace_log"])
    trace_path = tmp_path / "debug" / "run_trace.jsonl"
    assert trace_path.exists()
    assert "page_index_trace_line" in trace_path.read_text(encoding="utf-8")

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


def test_parse_source_uses_workflow_layered_lane(namespace_engines, monkeypatch):
    captured = {}

    def fake_run_workflow_layered_parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            semantic_tree=SimpleNamespace(title=kwargs["title"]),
            graph_payload={"nodes": [], "edges": []},
            evaluation={"basic_sense_verdict": "good"},
            diagnostics={"parser_lane": "workflow_layered", "parse_session_mode": "workflow_layered"},
            usage_summary={"provider": "openai", "model": "gpt4o", "total_cost": 0.0},
            layer_log=[{"stage": "workflow_layered_parse_start"}],
            parse_session={"mode": "workflow_layered"},
        )

    monkeypatch.setattr(
        "kogwistar_llm_wiki.ingest_pipeline.run_workflow_layered_parse",
        fake_run_workflow_layered_parse,
    )

    pipeline = IngestPipeline(namespace_engines)
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///contracts/acme.txt",
        title="Acme Contract",
        raw_text="Acme shall pay within 30 days.",
        parser_mode="openai",
        parser_lane="workflow_layered",
        llm_provider="azure_openai",
        llm_model="gpt4o",
    )

    result = pipeline.parse_source(request=request, source_document_id="doc-1")

    assert captured["source_document_id"] == "doc-1"
    assert captured["title"] == "Acme Contract"
    assert captured["raw_text"] == "Acme shall pay within 30 days."
    assert captured["provider_settings"].parser.provider == "azure"
    assert captured["provider_settings"].parser.model == "gpt4o"
    assert result.semantic_tree.title == "Acme Contract"
    assert result.layer_log[0]["stage"] == "workflow_layered_parse_start"
