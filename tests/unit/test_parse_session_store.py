from __future__ import annotations

import pytest
from kogwistar.engine_core.in_memory_meta import InMemoryMetaStore

from kogwistar_llm_wiki.parsing.parse_session_store import (
    ParseSessionStore,
    ParseSessionStoreConflict,
)
from kogwistar_llm_wiki.parsing.parse_views import (
    ParseFrontierItem,
    ParseSessionState,
    SourceRegion,
)


def _state() -> tuple[ParseSessionState, list[ParseFrontierItem]]:
    region = SourceRegion(source_document_id="rev-doc", start_char=0, end_char=5)
    item = ParseFrontierItem(
        frontier_id="frontier-1",
        session_id="session-1",
        generation_id="generation-1",
        workspace_id="demo",
        source_document_id="source-1",
        source_revision_id="revision-1",
        revision_document_id="rev-doc",
        region=region,
    )
    session = ParseSessionState(
        session_id="session-1",
        workspace_id="demo",
        source_document_id="source-1",
        source_revision_id="revision-1",
        source_digest="digest",
        revision_document_id="rev-doc",
        generation_id="generation-1",
        frontier_ids=(item.frontier_id,),
    )
    return session, [item]


def test_session_store_round_trips_and_retries_with_cas() -> None:
    store = ParseSessionStore(InMemoryMetaStore(), workspace_id="demo")
    session, frontier = _state()
    assert store.save(session, frontier, expected_version=None) == 1
    loaded = store.get(session.session_id)
    assert loaded is not None
    assert loaded[2] == 1
    assert loaded[1][0].frontier_id == "frontier-1"
    updated = session.model_copy(
        update={"consumed_frontier_ids": ("frontier-1",), "frontier_ids": ()}
    )
    assert store.save(updated, [], expected_version=1) == 2
    assert store.get(session.session_id)[0].consumed_frontier_ids == ("frontier-1",)
    with pytest.raises(ParseSessionStoreConflict):
        store.save(session, frontier, expected_version=1)


def test_session_store_rejects_cross_workspace_state() -> None:
    store = ParseSessionStore(InMemoryMetaStore(), workspace_id="demo")
    session, frontier = _state()
    with pytest.raises(ValueError, match="workspace"):
        store.save(session.model_copy(update={"workspace_id": "other"}), frontier, expected_version=None)


def test_session_store_read_rejects_payload_under_the_wrong_session_key() -> None:
    metadata = InMemoryMetaStore()
    store = ParseSessionStore(metadata, workspace_id="demo")
    session, frontier = _state()
    store.save(session, frontier, expected_version=None)
    row = metadata.get_named_projection(store.namespace, store.key(session.session_id))
    assert row is not None
    payload = dict(row["payload"])
    payload["session"] = session.model_copy(update={"session_id": "other-session"}).model_dump(
        mode="json"
    )
    metadata.compare_and_swap_named_projection(
        store.namespace,
        store.key(session.session_id),
        payload,
        expected_last_authoritative_seq=1,
        expected_last_materialized_seq=1,
        last_authoritative_seq=2,
        last_materialized_seq=2,
        projection_schema_version=store.schema_version,
        materialization_status="ready",
    )

    with pytest.raises(ValueError, match="identity does not match projection key"):
        store.get(session.session_id)


def test_session_store_rejects_frontier_from_another_session() -> None:
    store = ParseSessionStore(InMemoryMetaStore(), workspace_id="demo")
    session, frontier = _state()
    foreign = frontier[0].model_copy(update={"session_id": "other-session"})
    with pytest.raises(ValueError, match="does not belong"):
        store.save(session, [foreign], expected_version=None)


def test_session_store_rejects_frontier_pointer_divergence() -> None:
    store = ParseSessionStore(InMemoryMetaStore(), workspace_id="demo")
    session, frontier = _state()
    with pytest.raises(ValueError, match="frontier pointer"):
        store.save(session.model_copy(update={"frontier_ids": ()}), frontier, expected_version=None)


def test_session_store_rejects_callback_identity_pivot() -> None:
    store = ParseSessionStore(InMemoryMetaStore(), workspace_id="demo")
    session, frontier = _state()
    store.save(session, frontier, expected_version=None)
    pivoted = session.model_copy(update={"revision_document_id": "other-revision"})
    pivoted_frontier = [
        item.model_copy(update={"revision_document_id": "other-revision"}) for item in frontier
    ]
    with pytest.raises(ValueError, match="immutable identity|does not belong"):
        store.save(
            pivoted,
            pivoted_frontier,
            expected_version=1,
        )
