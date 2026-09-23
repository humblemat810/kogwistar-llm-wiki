from __future__ import annotations

import importlib.util
import os

import pytest

from kogwistar_llm_wiki.embeddings.multimodal_projection import (
    ChromaMultimodalProjectionStore,
    InMemoryMultimodalProjectionStore,
)
from kogwistar_llm_wiki.ingest.engine_builders import profile_isolated_postgres_schema
from kogwistar_llm_wiki.ingest_pipeline import _embedding_profile
from kg_doc_parser.workflow_ingest.providers import EmbeddingProviderConfig

pytestmark = [pytest.mark.integration, pytest.mark.ci_full, pytest.mark.slow]


def _profile(dimension: int, model: str):
    from llm_wiki_embedding_contract import EmbeddingProfile

    return EmbeddingProfile(
        provider="fake",
        model=model,
        embedding="single_vector",
        dimension=dimension,
    )


def _unit(view_id: str):
    from kogwistar_llm_wiki.embeddings.multimodal_projection import MultimodalSourceUnit

    return MultimodalSourceUnit(
        view_id=view_id,
        workspace_id="profile-smoke",
        source_id="source-1",
        source_revision_id="rev-1",
        modality="text",
        locator={"start_char": 0, "end_char": 4},
        text="test",
    )


def test_chroma_and_memory_profile_scopes_are_not_dimension_shared(tmp_path) -> None:
    """Always-run smoke for two incompatible spaces, with Chroma when installed."""

    profile_2d = _profile(2, "profile-a")
    profile_3d = _profile(3, "profile-b")
    unit = _unit("view-1")
    memory_2d = InMemoryMultimodalProjectionStore(scope="smoke", profile=profile_2d)
    memory_3d = InMemoryMultimodalProjectionStore(scope="smoke", profile=profile_3d)
    memory_2d.upsert_embedding(unit, ((1.0, 0.0),), profile=profile_2d)
    memory_3d.upsert_embedding(unit, ((1.0, 0.0, 0.0),), profile=profile_3d)
    assert memory_2d.projection_scope != memory_3d.projection_scope
    assert memory_2d.search(((1.0, 0.0),), profile=profile_2d)[0].view_id == "view-1"
    assert memory_3d.search(((1.0, 0.0, 0.0),), profile=profile_3d)[0].view_id == "view-1"

    if importlib.util.find_spec("chromadb") is None:
        return
    chroma_root = tmp_path / "chroma"
    chroma_2d = ChromaMultimodalProjectionStore(chroma_root, scope="smoke", profile=profile_2d)
    chroma_3d = ChromaMultimodalProjectionStore(chroma_root, scope="smoke", profile=profile_3d)
    chroma_2d.upsert_embedding(unit, ((1.0, 0.0),), profile=profile_2d)
    chroma_3d.upsert_embedding(unit, ((1.0, 0.0, 0.0),), profile=profile_3d)
    assert chroma_2d.search(((1.0, 0.0),), profile=profile_2d)[0].view_id == "view-1"
    assert chroma_3d.search(((1.0, 0.0, 0.0),), profile=profile_3d)[0].view_id == "view-1"
    chroma_2d.close()
    chroma_3d.close()


def test_live_pgvector_profiles_use_distinct_physical_schemas() -> None:
    """Real pgvector smoke, opt-in through a running PostgreSQL service."""

    dsn = os.getenv("LLM_WIKI_TEST_PG_DSN")
    if not dsn:
        pytest.skip("set LLM_WIKI_TEST_PG_DSN to run the live pgvector profile smoke")
    sqlalchemy = pytest.importorskip("sqlalchemy")
    pytest.importorskip("pgvector")
    from kogwistar.engine_core.postgres_backend import PgVectorBackend

    profile_2d = _embedding_profile(EmbeddingProviderConfig(provider="fake", model="pg-a", dimension=2))
    profile_3d = _embedding_profile(EmbeddingProviderConfig(provider="fake", model="pg-b", dimension=3))
    schema_2d = profile_isolated_postgres_schema("llm_wiki_smoke", profile_2d)
    schema_3d = profile_isolated_postgres_schema("llm_wiki_smoke", profile_3d)
    assert schema_2d != schema_3d
    engine = sqlalchemy.create_engine(dsn)
    try:
        backend_2d = PgVectorBackend(engine=engine, embedding_dim=2, schema=schema_2d)
        backend_3d = PgVectorBackend(engine=engine, embedding_dim=3, schema=schema_3d)
        backend_2d.nodes.upsert(
            ids=["profile-a"], documents=["two-dimensional"], metadatas=[{}], embeddings=[[1.0, 0.0]]
        )
        backend_3d.nodes.upsert(
            ids=["profile-b"], documents=["three-dimensional"], metadatas=[{}], embeddings=[[1.0, 0.0, 0.0]]
        )
        assert backend_2d.nodes.query(query_embeddings=[[1.0, 0.0]], n_results=1)["ids"][0][0] == "profile-a"
        assert backend_3d.nodes.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=1)["ids"][0][0] == "profile-b"
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema_2d}" CASCADE')
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema_3d}" CASCADE')
        engine.dispose()
