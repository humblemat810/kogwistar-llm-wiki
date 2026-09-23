from __future__ import annotations

import importlib.util
import os

import pytest

from kogwistar_llm_wiki.embeddings.multimodal_projection import (
    ChromaMultimodalProjectionStore,
    EmbeddingProfileMismatch,
    InMemoryMultimodalProjectionStore,
    PgVectorMultimodalProjectionStore,
)

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


def test_live_pgvector_profiles_use_distinct_physical_projection_tables() -> None:
    """Real pgvector smoke for the same multimodal contract as other stores."""

    dsn = os.getenv("LLM_WIKI_TEST_PG_DSN")
    if not dsn:
        pytest.skip("set LLM_WIKI_TEST_PG_DSN to run the live pgvector profile smoke")
    sqlalchemy = pytest.importorskip("sqlalchemy")
    pytest.importorskip("pgvector")

    profile_2d = _profile(2, "pg-a")
    profile_3d = _profile(3, "pg-b")
    engine = sqlalchemy.create_engine(dsn)
    store_2d = PgVectorMultimodalProjectionStore(
        engine=engine, scope="profile-smoke", profile=profile_2d
    )
    store_3d = PgVectorMultimodalProjectionStore(
        engine=engine, scope="profile-smoke", profile=profile_3d
    )
    try:
        store_2d.upsert_embedding(_unit("view-2d"), ((1.0, 0.0),), profile=profile_2d)
        store_3d.upsert_embedding(_unit("view-3d"), ((1.0, 0.0, 0.0),), profile=profile_3d)
        assert store_2d.projection_scope != store_3d.projection_scope
        assert store_2d.search(((1.0, 0.0),), profile=profile_2d)[0].view_id == "view-2d"
        assert store_3d.search(((1.0, 0.0, 0.0),), profile=profile_3d)[0].view_id == "view-3d"
        assert store_2d.stage_counts() == {"stage1": 1, "stage2": 1, "pending_stage2": 0}
        assert store_3d.get("view-3d", profile=profile_3d) is not None
        with pytest.raises(EmbeddingProfileMismatch):
            store_2d.search(((1.0, 0.0, 0.0),), profile=profile_3d)
    finally:
        store_2d.close()
        store_3d.close()
        store_2d._metadata.drop_all(engine)
        store_3d._metadata.drop_all(engine)
        engine.dispose()
