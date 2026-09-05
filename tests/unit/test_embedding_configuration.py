from __future__ import annotations

import pytest

from kg_doc_parser.workflow_ingest.providers import EmbeddingProviderConfig
from kogwistar_llm_wiki import ingest_pipeline


def test_namespace_builders_keep_tiny_embedding_as_default() -> None:
    embedding, config = ingest_pipeline._resolve_embedding_function()

    assert embedding.name() == "kogwistar-llm-wiki-embedding-v1"
    assert config.provider == "fake"
    assert config.dimension == 2
    assert len(embedding(["sample"])[0]) == 2


def test_embedding_provider_knobs_use_shared_provider_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[EmbeddingProviderConfig] = []

    class FakeEmbedding:
        def name(self) -> str:
            return "ollama:qwen3-embedding:0.6b"

        def __call__(self, input: list[str]) -> list[list[float]]:
            return [[1.0, 2.0, 3.0] for _ in input]

    def fake_factory(config: EmbeddingProviderConfig) -> FakeEmbedding:
        calls.append(config)
        return FakeEmbedding()

    monkeypatch.setattr(ingest_pipeline, "build_embedding_function", fake_factory)
    embedding, config = ingest_pipeline._resolve_embedding_function(
        embedding_provider="ollama",
        embedding_model="qwen3-embedding:0.6b",
        embedding_dimension=3,
        embedding_base_url="http://127.0.0.1:11434",
    )

    assert calls == [config]
    assert config.provider == "ollama"
    assert config.model == "qwen3-embedding:0.6b"
    assert config.dimension == 3
    assert config.base_url == "http://127.0.0.1:11434"
    assert embedding.name() == "ollama:qwen3-embedding:0.6b"


def test_embedding_environment_is_used_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KG_DOC_EMBED_PROVIDER", "ollama")
    monkeypatch.setenv("KG_DOC_EMBED_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("KG_DOC_EMBED_DIMENSION", "1024")
    monkeypatch.setenv("KG_DOC_EMBED_BASE_URL", "http://localhost:11434")

    expected = EmbeddingProviderConfig(
        provider="ollama",
        model="qwen3-embedding:0.6b",
        dimension=1024,
        base_url="http://localhost:11434",
    )
    monkeypatch.setattr(
        ingest_pipeline,
        "build_embedding_function",
        lambda config: (lambda values: [[0.0] * config.dimension for _ in values]),
    )

    _, config = ingest_pipeline._resolve_embedding_function()

    assert config == expected


def test_llm_wiki_embedding_environment_overrides_parser_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KG_DOC_EMBED_PROVIDER", "ollama")
    monkeypatch.setenv("KG_DOC_EMBED_MODEL", "parser-model")
    monkeypatch.setenv("KG_DOC_EMBED_DIMENSION", "1024")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_PROVIDER", "fake")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_MODEL", "wiki-test-model")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_DIMENSION", "7")

    _, config = ingest_pipeline._resolve_embedding_function()

    assert config.provider == "fake"
    assert config.model == "wiki-test-model"
    assert config.dimension == 7


def test_embedding_configs_are_independent_per_graph_space() -> None:
    configs = {
        "conversation": EmbeddingProviderConfig(
            provider="fake", model="conversation-model", dimension=3
        ),
        "knowledge": EmbeddingProviderConfig(
            provider="fake", model="knowledge-model", dimension=5
        ),
        "workflow": EmbeddingProviderConfig(
            provider="fake", model="runtime-model", dimension=4
        ),
    }

    functions, resolved = ingest_pipeline._resolve_embedding_functions(
        embedding_configs=configs
    )

    assert resolved["conversation"].model == "conversation-model"
    assert resolved["knowledge"].model == "knowledge-model"
    assert resolved["workflow"].model == "runtime-model"
    assert len(functions["conversation"](["x"])[0]) == 3
    assert len(functions["knowledge"](["x"])[0]) == 5
    assert len(functions["workflow"](["x"])[0]) == 4
    assert resolved["wisdom"].model == "kogwistar-llm-wiki-embedding-v1"


def test_postgres_real_embedding_requires_dimension() -> None:
    with pytest.raises(ValueError, match="embedding_dim is required"):
        ingest_pipeline.build_postgres_namespace_engines(
            base_dir="unused",
            dsn="postgresql://localhost/example",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding:0.6b",
        )
