from __future__ import annotations

import pytest
from pathlib import Path

from kg_doc_parser.workflow_ingest.providers import EmbeddingProviderConfig
from kogwistar_llm_wiki import ingest_pipeline
from kogwistar_llm_wiki.worker import MaintenanceWorker


def test_namespace_builders_keep_tiny_embedding_as_default() -> None:
    embedding, config = ingest_pipeline._resolve_embedding_function()

    assert embedding.name() == "kogwistar-llm-wiki-embedding-v1"
    assert config.provider == "fake"
    assert config.dimension == 2
    assert len(embedding(["sample"])[0]) == 2


def test_embedding_profile_exposes_token_context_metadata() -> None:
    config = EmbeddingProviderConfig(
        provider="fake",
        model="token-aware",
        dimension=3,
        max_sequence_length=8192,
        crop_token_budget=7680,
        tokenizer_fingerprint="tokenizer-v1",
    )
    profile = ingest_pipeline._embedding_profile(config)

    assert profile.max_sequence_length == 8192
    assert profile.crop_token_budget == 7680
    assert profile.tokenizer_fingerprint == "tokenizer-v1"
    assert profile.crop_policy == "token_prefix"
    assert profile.fingerprint


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


def test_global_llm_wiki_embedding_scope_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_PROVIDER", "fake")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_MODEL", "global-model")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_EMBED_DIMENSION", "6")

    _, configs = ingest_pipeline._resolve_embedding_functions()

    assert configs["knowledge"].model == "global-model"
    assert configs["knowledge"].dimension == 6


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


def test_postgres_real_embedding_requires_dimension(tmp_path: Path) -> None:
    data_dir = tmp_path / "missing-dimension"
    with pytest.raises(ValueError, match="embedding dimension is required"):
        ingest_pipeline.build_postgres_namespace_engines(
            base_dir=data_dir,
            dsn="postgresql://localhost/example",
            embedding_provider="ollama",
            embedding_model="qwen3-embedding:0.6b",
        )
    assert not data_dir.exists()


def test_unsupported_embedding_scope_fails_before_postgres_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "must-not-initialize"
    monkeypatch.setenv(
        "KOGWISTAR_LLM_WIKI_MAINTENANCE_EMBED_PROVIDER", "ollama"
    )

    with pytest.raises(ValueError, match="unsupported llm-wiki embedding scope"):
        ingest_pipeline.build_postgres_namespace_engines(
            base_dir=data_dir,
            dsn="postgresql://localhost/example",
        )

    assert not data_dir.exists()


def test_non_postgres_builder_also_rejects_real_provider_without_dimension(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "missing-dimension-chroma"

    with pytest.raises(ValueError, match="embedding dimension is required"):
        ingest_pipeline.build_persistent_namespace_engines(
            base_dir=data_dir,
            embedding_provider="ollama",
            embedding_model="qwen3-embedding:0.6b",
        )

    assert not data_dir.exists()


def test_postgres_requires_one_embedding_profile_for_shared_vector_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dimensions: dict[str, int] = {}

    def fake_postgres_engine(*args: object, **kwargs: object) -> object:
        dimensions[str(kwargs["kg_graph_type"])] = int(kwargs["embedding_dim"])
        return object()

    monkeypatch.setattr(ingest_pipeline, "_build_postgres_engine", fake_postgres_engine)
    with pytest.raises(ValueError, match="share physical pgvector tables"):
        ingest_pipeline.build_postgres_namespace_engines(
            base_dir=tmp_path / "must-not-initialize",
            dsn="postgresql://localhost/example",
            embedding_configs={
                "conversation": EmbeddingProviderConfig(provider="fake", dimension=3),
                "workflow": EmbeddingProviderConfig(provider="fake", dimension=4),
                "knowledge": EmbeddingProviderConfig(provider="fake", dimension=5),
                "wisdom": EmbeddingProviderConfig(provider="fake", dimension=6),
            },
    )

    assert dimensions == {}
    assert not (tmp_path / "must-not-initialize").exists()


def test_postgres_rejects_mixed_models_with_matching_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        ingest_pipeline,
        "_build_postgres_engine",
        lambda *args, **kwargs: pytest.fail("PostgreSQL backend must not initialize"),
    )

    with pytest.raises(ValueError, match="embedding profiles differ"):
        ingest_pipeline.build_postgres_namespace_engines(
            base_dir=tmp_path / "must-not-initialize-model",
            dsn="postgresql://localhost/example",
            embedding_configs={
                "conversation": EmbeddingProviderConfig(provider="fake", model="history", dimension=5),
                "workflow": EmbeddingProviderConfig(provider="fake", model="runtime", dimension=5),
                "knowledge": EmbeddingProviderConfig(provider="fake", model="knowledge", dimension=5),
                "wisdom": EmbeddingProviderConfig(provider="fake", model="wisdom", dimension=5),
            },
        )

    assert not (tmp_path / "must-not-initialize-model").exists()


def test_postgres_reuses_one_profile_for_each_shared_graph_space(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dimensions: dict[str, int] = {}

    def fake_postgres_engine(*args: object, **kwargs: object) -> object:
        dimensions[str(kwargs["kg_graph_type"])] = int(kwargs["embedding_dim"])
        return object()

    monkeypatch.setattr(ingest_pipeline, "_build_postgres_engine", fake_postgres_engine)
    ingest_pipeline.build_postgres_namespace_engines(
        base_dir=tmp_path / "shared-profile",
        dsn="postgresql://localhost/example",
        embedding_configs={
            "conversation": EmbeddingProviderConfig(provider="fake", model="shared", dimension=5),
            "workflow": EmbeddingProviderConfig(provider="fake", model="shared", dimension=5),
            "knowledge": EmbeddingProviderConfig(provider="fake", model="shared", dimension=5),
            "wisdom": EmbeddingProviderConfig(provider="fake", model="shared", dimension=5),
        },
    )

    assert dimensions == {
        "conversation": 5,
        "workflow": 5,
        "knowledge": 5,
        "wisdom": 5,
    }


def test_postgres_engine_passes_dimension_to_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[int] = []

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            captured.append(int(kwargs["embedding_dim"]))

    monkeypatch.setattr(
        "kogwistar.engine_core.engine_postgres.EnginePostgresConfig", FakeConfig
    )
    monkeypatch.setattr(
        "kogwistar.engine_core.engine_postgres.build_postgres_backend",
        lambda config: (object(), object()),
    )
    monkeypatch.setattr(ingest_pipeline, "GraphKnowledgeEngine", lambda **kwargs: kwargs)

    ingest_pipeline._build_postgres_engine(
        tmp_path,
        kg_graph_type="knowledge",
        embedding_function=lambda values: [[0.0] for _ in values],
        dsn="postgresql://localhost/example",
        embedding_dim=7,
        schema="public",
    )

    assert captured == [7]


def test_frontend_knowledge_and_maintenance_use_their_declared_spaces() -> None:
    engines = ingest_pipeline.build_in_memory_namespace_engines(
        embedding_configs={
            "conversation": EmbeddingProviderConfig(provider="fake", dimension=2),
            "workflow": EmbeddingProviderConfig(provider="fake", dimension=4),
            "knowledge": EmbeddingProviderConfig(provider="fake", dimension=3),
            "wisdom": EmbeddingProviderConfig(provider="fake", dimension=5),
        }
    )
    try:
        assert len(engines.conversation.embedding_function(["frontend"])[0]) == 2
        assert len(engines.kg.embedding_function(["knowledge"])[0]) == 3
        assert len(engines.workflow.embedding_function(["maintenance-state"])[0]) == 4

        worker = MaintenanceWorker(engines)
        # Maintenance state is 4D, but the worker's conversation replies use
        # the shared conversation/history engine and therefore remain 2D.
        assert worker.runtime.conversation_engine is engines.conversation
        assert len(worker.runtime.conversation_engine.embedding_function(["reply"])[0]) == 2
    finally:
        engines.close()
