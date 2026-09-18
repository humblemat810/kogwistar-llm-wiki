from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kogwistar.engine_core.in_memory_meta import InMemoryMetaStore
from pydantic import ValidationError

from kogwistar_llm_wiki.parse_generation_store import ParseGenerationStore
from kogwistar_llm_wiki.parse_views import (
    ParseFrontierItem,
    ParseGeneration,
    ParseGenerationCommit,
    ParseGenerationMember,
    ParseSessionState,
    ParseTarget,
    ParseView,
    ParseViewConflict,
    ParseViewResolver,
    ParseViewSelection,
    ParseViewStore,
    SourceRegion,
    frontier_id,
    generation_id,
    generation_member_id,
    legacy_generation_id,
    reparse_session_id,
)


def _region(start: int, end: int) -> SourceRegion:
    return SourceRegion(source_document_id="rev-doc", start_char=start, end_char=end)


def _view(version: int, *, selection: tuple[ParseViewSelection, ...] = ()) -> ParseView:
    return ParseView(
        view_id=f"view-{version}",
        view_version=version,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        revision_document_id="rev-doc",
        selections=selection,
    )


def _commit_member(metadata: InMemoryMetaStore, *, member_id: str = "member-a") -> None:
    generation = ParseGeneration(
        generation_id="g1",
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        source_digest="digest",
        revision_document_id="rev-doc",
        parser_profile="coarse-v1",
        parser_version="1",
        status="stable",
    )
    member = ParseGenerationMember(
        member_id=member_id,
        generation_id=generation.generation_id,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        revision_document_id="rev-doc",
        region=_region(0, 10),
    )
    commit = ParseGenerationCommit(
        commit_id="commit-1",
        generation_id=generation.generation_id,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        member_ids=(member_id,),
    )
    ParseGenerationStore(metadata, workspace_id="demo").commit(generation, commit, [member])


def _commit_region(
    metadata: InMemoryMetaStore,
    *,
    generation_id_value: str,
    parser_profile: str,
    member_id: str,
    commit_id: str,
    start_char: int,
    end_char: int,
) -> ParseGenerationMember:
    generation = ParseGeneration(
        generation_id=generation_id_value,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        source_digest="digest",
        revision_document_id="rev-doc",
        parser_profile=parser_profile,
        parser_version="1",
        status="stable",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    member = ParseGenerationMember(
        member_id=member_id,
        generation_id=generation_id_value,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        revision_document_id="rev-doc",
        region=_region(start_char, end_char),
    )
    commit = ParseGenerationCommit(
        commit_id=commit_id,
        generation_id=generation_id_value,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        member_ids=(member_id,),
    )
    ParseGenerationStore(metadata, workspace_id="demo").commit(generation, commit, [member])
    return member


def test_parse_evidence_ids_are_stable_but_generation_members_are_event_scoped() -> None:
    generation = generation_id(
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        parser_profile="coarse-v1",
        derivation_id="session-a",
    )
    assert generation == generation_id(
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        parser_profile="coarse-v1",
        derivation_id="session-a",
    )
    assert generation != generation_id(
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-2",
        parser_profile="coarse-v1",
        derivation_id="session-a",
    )
    assert generation != generation_id(
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        parser_profile="coarse-v1",
        derivation_id="session-b",
    )
    assert generation_member_id(generation_id=generation, commit_id="commit-a", ordinal=0) != generation_member_id(
        generation_id=generation, commit_id="commit-b", ordinal=0
    )
    assert legacy_generation_id(workspace_id="demo", source_document_id="logical-source")


def test_parse_view_rejects_overlap_and_wrong_revision_document() -> None:
    first = ParseViewSelection(member_id="member-a", generation_id="g1", region=_region(0, 10))
    second = ParseViewSelection(member_id="member-b", generation_id="g1", region=_region(9, 20))
    with pytest.raises(ValidationError, match="must not overlap"):
        _view(1, selection=(first, second))
    wrong = ParseViewSelection(
        member_id="member-c",
        generation_id="g1",
        region=SourceRegion(source_document_id="old-rev", start_char=20, end_char=30),
    )
    with pytest.raises(ValidationError, match="revision document"):
        _view(1, selection=(wrong,))


def test_parse_view_activation_is_per_source_and_cas_protected() -> None:
    store = ParseViewStore(InMemoryMetaStore(), workspace_id="demo")
    assert store.get("logical-source") is None
    assert store.activate(_view(1), expected_view_version=None) is True
    assert store.get("logical-source").view_id == "view-1"
    with pytest.raises(ParseViewConflict, match="expected no existing"):
        store.activate(_view(2), expected_view_version=None)
    with pytest.raises(ParseViewConflict):
        store.activate(_view(3), expected_view_version=2)
    assert store.activate(_view(2), expected_view_version=1) is True
    assert store.get("logical-source").view_version == 2
    with pytest.raises(ValueError, match="must increase"):
        store.activate(_view(2), expected_view_version=2)


def test_parse_view_resolver_uses_legacy_g0_until_a_view_is_activated() -> None:
    metadata = InMemoryMetaStore()
    resolver = ParseViewResolver(metadata, workspace_id="demo")
    legacy = resolver.resolve("logical-source", fallback_revision_document_id="rev-doc-1")
    assert legacy.is_legacy is True
    assert legacy.revision_document_id == "rev-doc-1"
    assert legacy.generation_ids[0] == legacy_generation_id(
        workspace_id="demo", source_document_id="logical-source"
    )

    store = ParseViewStore(metadata, workspace_id="demo")
    store.activate(_view(1), expected_view_version=None)
    active = resolver.resolve("logical-source", fallback_revision_document_id="rev-doc-2")
    assert active.is_legacy is False
    assert active.view_id == "view-1"
    assert active.revision_document_id == "rev-doc"
    assert resolver.is_active_revision("logical-source", "rev-doc") is True
    assert resolver.is_active_revision("logical-source", "rev-doc-2") is False


def test_selective_reparse_can_replace_one_region_without_dropping_neighbors() -> None:
    metadata = InMemoryMetaStore()
    old_a = _commit_region(
        metadata,
        generation_id_value="g1",
        parser_profile="coarse-v1",
        member_id="old-a",
        commit_id="commit-a",
        start_char=0,
        end_char=10,
    )
    old_b = _commit_region(
        metadata,
        generation_id_value="g1",
        parser_profile="coarse-v1",
        member_id="old-b",
        commit_id="commit-b",
        start_char=10,
        end_char=20,
    )
    old_c = _commit_region(
        metadata,
        generation_id_value="g1",
        parser_profile="coarse-v1",
        member_id="old-c",
        commit_id="commit-c",
        start_char=20,
        end_char=30,
    )
    new_b = _commit_region(
        metadata,
        generation_id_value="g2",
        parser_profile="repair-v2",
        member_id="new-b",
        commit_id="commit-new-b",
        start_char=10,
        end_char=20,
    )
    store = ParseViewStore(metadata, workspace_id="demo")
    store.activate(
        _view(
            1,
            selection=tuple(
                ParseViewSelection(member_id=member.member_id, generation_id="g1", region=member.region)
                for member in (old_a, old_b, old_c)
            ),
        ),
        expected_view_version=None,
    )

    store.activate(
        _view(
            2,
            selection=(
                ParseViewSelection(member_id=old_a.member_id, generation_id="g1", region=old_a.region),
                ParseViewSelection(member_id=new_b.member_id, generation_id="g2", region=new_b.region),
                ParseViewSelection(member_id=old_c.member_id, generation_id="g1", region=old_c.region),
            ),
        ),
        expected_view_version=1,
    )

    resolved = ParseViewResolver(metadata, workspace_id="demo").resolve("logical-source")
    assert resolved.view_version == 2
    assert resolved.member_ids == ("old-a", "new-b", "old-c")
    assert resolved.generation_ids == ("g1", "g2")
    assert ParseGenerationStore(metadata, workspace_id="demo").get("g1") is not None


def test_parse_view_resolver_requires_selected_member_after_activation() -> None:
    metadata = InMemoryMetaStore()
    resolver = ParseViewResolver(metadata, workspace_id="demo")
    selection = ParseViewSelection(member_id="member-a", generation_id="g1", region=_region(0, 10))
    _commit_member(metadata)
    ParseViewStore(metadata, workspace_id="demo").activate(
        _view(1, selection=(selection,)), expected_view_version=None
    )

    assert resolver.is_active_metadata(
        "logical-source",
        {"source_revision_document_id": "rev-doc"},
    ) is False
    assert resolver.is_active_metadata(
        "logical-source",
        {"source_revision_document_id": "rev-doc", "parse_generation_member_id": "member-a"},
    ) is True
    assert resolver.is_active_metadata(
        "logical-source",
        {"source_revision_document_id": "rev-doc", "parse_generation_member_id": "member-b"},
    ) is False
    assert resolver.is_active_metadata(
        "logical-source",
        {"source_revision_document_id": "old-rev-doc"},
    ) is False


def test_parse_view_activation_rejects_uncommitted_or_foreign_evidence() -> None:
    metadata = InMemoryMetaStore()
    store = ParseViewStore(metadata, workspace_id="demo")
    selection = ParseViewSelection(member_id="missing", generation_id="g1", region=_region(0, 10))
    with pytest.raises((TypeError, ValueError), match="unknown generation"):
        store.activate(_view(1, selection=(selection,)), expected_view_version=None)

    _commit_member(metadata)
    foreign = ParseGenerationMember(
        member_id="foreign-member",
        generation_id="g1",
        workspace_id="other",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        revision_document_id="rev-doc",
        region=_region(0, 10),
    )
    generation_row = metadata.get_named_projection(
        "ws:demo:projection_state", "parse_generation:g1"
    )
    assert generation_row is not None
    generation_payload = dict(generation_row["payload"])
    generation_payload["members"] = {
        **generation_payload["members"],
        foreign.member_id: foreign.model_dump(mode="json"),
    }
    metadata.replace_named_projection(
        "ws:demo:projection_state",
        "parse_generation:g1",
        generation_payload,
        last_authoritative_seq=generation_row["last_authoritative_seq"],
        last_materialized_seq=generation_row["last_materialized_seq"],
        projection_schema_version=generation_row["projection_schema_version"],
        materialization_status=generation_row["materialization_status"],
    )
    with pytest.raises(ValueError, match="not grounded in its generation member"):
        store.activate(
            _view(
                1,
                selection=(
                    ParseViewSelection(
                        member_id="foreign-member", generation_id="g1", region=_region(0, 10)
                    ),
                ),
            ),
            expected_view_version=None,
        )


def test_parse_view_activation_rejects_member_key_identity_mismatch() -> None:
    metadata = InMemoryMetaStore()
    _commit_member(metadata)
    generation_row = metadata.get_named_projection(
        "ws:demo:projection_state", "parse_generation:g1"
    )
    assert generation_row is not None
    generation_payload = dict(generation_row["payload"])
    generation_payload["members"] = {
        "member-a": ParseGenerationMember(
            member_id="different-member",
            generation_id="g1",
            workspace_id="demo",
            source_document_id="logical-source",
            source_revision_id="revision-1",
            revision_document_id="rev-doc",
            region=_region(0, 10),
        ).model_dump(mode="json")
    }
    metadata.replace_named_projection(
        "ws:demo:projection_state",
        "parse_generation:g1",
        generation_payload,
        last_authoritative_seq=generation_row["last_authoritative_seq"],
        last_materialized_seq=generation_row["last_materialized_seq"],
        projection_schema_version=generation_row["projection_schema_version"],
        materialization_status=generation_row["materialization_status"],
    )
    with pytest.raises(ValueError, match="not grounded"):
        ParseViewStore(metadata, workspace_id="demo").activate(
            _view(
                1,
                selection=(
                    ParseViewSelection(member_id="member-a", generation_id="g1", region=_region(0, 10)),
                ),
            ),
            expected_view_version=None,
        )


def test_parse_view_activation_rejects_member_missing_from_generation_commit() -> None:
    metadata = InMemoryMetaStore()
    _commit_member(metadata)
    generation_row = metadata.get_named_projection(
        "ws:demo:projection_state", "parse_generation:g1"
    )
    assert generation_row is not None
    generation_payload = dict(generation_row["payload"])
    generation_payload["commits"] = {}
    metadata.replace_named_projection(
        "ws:demo:projection_state",
        "parse_generation:g1",
        generation_payload,
        last_authoritative_seq=generation_row["last_authoritative_seq"],
        last_materialized_seq=generation_row["last_materialized_seq"],
        projection_schema_version=generation_row["projection_schema_version"],
        materialization_status=generation_row["materialization_status"],
    )
    selection = ParseViewSelection(member_id="member-a", generation_id="g1", region=_region(0, 10))
    with pytest.raises(ValueError, match="uncommitted generation member"):
        ParseViewStore(metadata, workspace_id="demo").activate(
            _view(1, selection=(selection,)), expected_view_version=None
        )


def test_parse_target_is_revision_pinned_and_has_distinct_session_scope() -> None:
    target = ParseTarget(
        source_document_id="logical-source",
        source_revision_id="revision-1",
        revision_document_id="rev-doc",
        region=_region(10, 20),
        reason="parser profile upgrade",
        parser_profile="workflow-v2",
    )
    assert reparse_session_id(
        workspace_id="demo",
        source_document_id=target.source_document_id,
        source_revision_id=target.source_revision_id,
        parser_profile=target.parser_profile,
        region=target.region,
    ) != reparse_session_id(
        workspace_id="demo",
        source_document_id=target.source_document_id,
        source_revision_id=target.source_revision_id,
        parser_profile=target.parser_profile,
        region=_region(20, 30),
    )
    with pytest.raises(ValidationError, match="revision document"):
        ParseTarget(
            **(
                target.model_dump()
                | {"region": {"source_document_id": "other", "start_char": 0, "end_char": 1}}
            )
        )


def test_frontier_and_session_are_bounded_and_serializable() -> None:
    region = _region(0, 20)
    item = ParseFrontierItem(
        frontier_id=frontier_id(session_id="session-1", region=region, ordinal=0),
        session_id="session-1",
        generation_id="g1",
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        revision_document_id="rev-doc",
        region=region,
        depth=1,
    )
    session = ParseSessionState(
        session_id="session-1",
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        source_digest="digest",
        revision_document_id="rev-doc",
        generation_id="g1",
        frontier_ids=(item.frontier_id,),
        max_frontier_items=1,
        max_depth=2,
    )
    assert ParseFrontierItem.model_validate(item.model_dump()) == item
    assert ParseSessionState.model_validate(session.model_dump()) == session
    with pytest.raises(ValidationError):
        ParseSessionState.model_validate(session.model_dump() | {"max_frontier_items": 0})
    generation = ParseGeneration(
        generation_id="g1",
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        source_digest="digest",
        revision_document_id="rev-doc",
        parser_profile="coarse-v1",
        parser_version="1",
    )
    commit = ParseGenerationCommit(
        commit_id="commit-1",
        generation_id=generation.generation_id,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        member_ids=("member-1",),
    )
    assert generation.model_validate(generation.model_dump()) == generation
    assert commit.model_validate(commit.model_dump()) == commit
