from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kg_doc_parser.workflow_ingest.providers import EmbeddingProviderConfig

from kogwistar_llm_wiki import ingest_pipeline


def _embedding_configs() -> dict[str, EmbeddingProviderConfig]:
    return {
        space: EmbeddingProviderConfig(provider="fake", model="shared", dimension=2)
        for space in ("conversation", "workflow", "knowledge", "wisdom")
    }


def test_postgres_namespace_builder_forwards_acl_setting_to_all_engines(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_engine(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(ingest_pipeline, "_build_postgres_engine", fake_engine)
    ingest_pipeline.build_postgres_namespace_engines(
        base_dir=tmp_path / "acl-enabled",
        dsn="postgresql://localhost/example",
        embedding_function=lambda values: [[0.0, 1.0] for _ in values],
        embedding_configs=_embedding_configs(),
        acl_enabled=True,
    )

    assert len(calls) == 4
    assert {call["kg_graph_type"] for call in calls} == {
        "conversation",
        "workflow",
        "knowledge",
        "wisdom",
    }
    assert all(call["acl_enabled"] is True for call in calls)


def test_postgres_namespace_builder_keeps_custom_factory_compatibility_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def legacy_engine(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(ingest_pipeline, "_build_postgres_engine", legacy_engine)
    ingest_pipeline.build_postgres_namespace_engines(
        base_dir=tmp_path / "acl-default-off",
        dsn="postgresql://localhost/example",
        embedding_function=lambda values: [[0.0, 1.0] for _ in values],
        embedding_configs=_embedding_configs(),
    )

    assert len(calls) == 4
    assert all("acl_enabled" not in call for call in calls)


def test_postgres_namespace_builder_assigns_isolated_embedding_cache_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_engine(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    cache_dir = tmp_path / "vector-cache"
    monkeypatch.setattr(ingest_pipeline, "_build_postgres_engine", fake_engine)
    ingest_pipeline.build_postgres_namespace_engines(
        base_dir=tmp_path / "app-data",
        dsn="postgresql://localhost/example",
        embedding_function=lambda values: [[0.0, 1.0] for _ in values],
        embedding_configs=_embedding_configs(),
        embedding_cache_dir=cache_dir,
    )

    paths = {str(call["kg_graph_type"]): call["embedding_cache_path"] for call in calls}
    assert paths == {
        space: str(cache_dir / space)
        for space in ("conversation", "workflow", "knowledge", "wisdom")
    }


def test_postgres_engine_passes_acl_to_core_constructor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            captured["backend_config"] = kwargs

    def fake_engine(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(
        "kogwistar.engine_core.engine_postgres.EnginePostgresConfig", FakeConfig
    )
    monkeypatch.setattr(
        "kogwistar.engine_core.engine_postgres.build_postgres_backend",
        lambda config: (object(), object()),
    )
    monkeypatch.setattr(ingest_pipeline, "GraphKnowledgeEngine", fake_engine)

    ingest_pipeline._build_postgres_engine(
        tmp_path,
        kg_graph_type="knowledge",
        embedding_function=lambda values: [[0.0, 1.0] for _ in values],
        dsn="postgresql://localhost/example",
        embedding_dim=2,
        schema="public",
        acl_enabled=True,
        embedding_cache_path=str(tmp_path / "cache" / "knowledge"),
    )

    assert captured["acl_enabled"] is True
    assert captured["embedding_cache_path"] == str(tmp_path / "cache" / "knowledge")
