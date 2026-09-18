from __future__ import annotations

from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.ingest_pipeline import IngestPipelineRequest
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.parsing.parse_generation_store import ParseGenerationStore
from kogwistar_llm_wiki.parsing.parse_session_store import ParseSessionStore
from kogwistar_llm_wiki.parsing.parse_views import (
    ParseFrontierItem,
    ParseGeneration,
    ParseGenerationCommit,
    ParseGenerationMember,
    ParseSessionPhase,
    ParseSessionState,
    ParseView,
    ParseViewResolver,
    ParseViewSelection,
    ParseViewStore,
    SourceRegion,
)
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker


def test_durable_frontier_write_is_member_tagged_before_view_activation(
    pipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    request = ingest_request.model_copy(
        update={"operation_mode": "maintenance_first", "parser_lane": "page_index"}
    )
    source_document_id = pipeline._source_document_id(request)
    namespaces = WorkspaceNamespaces(request.workspace_id)
    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=namespaces.conv_bg,
    )
    revision = pipeline.source_revision(request=request, source_document_id=source_document_id)
    session = pipeline.initialize_durable_parse_session(
        request=request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id or source_document_id,
        revision=revision,
    )
    stored = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).get(session.session_id)
    assert stored is not None

    worker = MaintenanceWorker(pipeline.engines)
    result = worker._expand_durable_parse_frontier(
        SimpleNamespace(workspace_id=request.workspace_id, job_id="job-1", maintenance_kind="document_expand_parse_children"),
        stored[0],
        stored[1],
    )

    member_id = result["members"][0]["member_id"]
    with _temporary_namespace(pipeline.engines.kg, namespaces.source_space):
        nodes = pipeline.engines.kg.read.get_nodes(
            where={"parse_generation_member_id": member_id},
            limit=100,
        )
    assert nodes
    resolver = ParseViewResolver(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    assert resolver.is_active_metadata(source_document_id, nodes[0].metadata) is False
    assert result["parse_view"]["selections"][0]["member_id"] == member_id
    worker._record_durable_parse_readiness(
        SimpleNamespace(workspace_id=request.workspace_id),
        session,
    )
    with _temporary_namespace(pipeline.engines.kg, namespaces.source_space):
        readiness = pipeline.engines.kg.read.get_nodes(
            where={
                "artifact_kind": "source_readiness",
                "source_document_id": source_document_id,
                "readiness_stage": "parsed_graph_persisted",
            },
            limit=10,
        )
    assert readiness


def test_duplicate_frontier_delivery_reuses_generation_event_identity(
    pipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    request = ingest_request.model_copy(
        update={"operation_mode": "maintenance_first", "parser_lane": "page_index"}
    )
    source_document_id = pipeline._source_document_id(request)
    namespaces = WorkspaceNamespaces(request.workspace_id)
    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=namespaces.conv_bg,
    )
    revision = pipeline.source_revision(request=request, source_document_id=source_document_id)
    session = pipeline.initialize_durable_parse_session(
        request=request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id or source_document_id,
        revision=revision,
    )
    stored = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).get(session.session_id)
    assert stored is not None
    worker = MaintenanceWorker(pipeline.engines)
    first = worker._expand_durable_parse_frontier(
        SimpleNamespace(
            workspace_id=request.workspace_id,
            job_id="delivery-a",
            maintenance_kind="document_expand_parse_children",
        ),
        stored[0],
        stored[1],
    )
    generation_store = ParseGenerationStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    generation_store.commit(
        ParseGeneration.model_validate(first["generation"]),
        ParseGenerationCommit.model_validate(first["commit"]),
        [ParseGenerationMember.model_validate(item) for item in first["members"]],
    )
    second = worker._expand_durable_parse_frontier(
        SimpleNamespace(
            workspace_id=request.workspace_id,
            job_id="delivery-b",
            maintenance_kind="document_expand_parse_children",
        ),
        stored[0],
        stored[1],
    )
    assert first["commit"]["commit_id"] == second["commit"]["commit_id"]
    assert first["generation"] == second["generation"]
    assert first["members"][0]["member_id"] == second["members"][0]["member_id"]
    member_id = first["members"][0]["member_id"]
    with _temporary_namespace(pipeline.engines.kg, namespaces.source_space):
        first_nodes = pipeline.engines.kg.read.get_nodes(
            where={"parse_generation_member_id": member_id},
            limit=100,
        )
    assert first_nodes
    assert len({str(node.id) for node in first_nodes}) == len(first_nodes)


def test_pending_parse_view_recovers_after_session_write_before_activation(
    pipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    request = ingest_request.model_copy(
        update={"operation_mode": "maintenance_first", "parser_lane": "page_index"}
    )
    source_document_id = pipeline._source_document_id(request)
    namespaces = WorkspaceNamespaces(request.workspace_id)
    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=namespaces.conv_bg,
    )
    revision = pipeline.source_revision(request=request, source_document_id=source_document_id)
    session = pipeline.initialize_durable_parse_session(
        request=request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id or source_document_id,
        revision=revision,
    )
    session_store = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    stored = session_store.get(session.session_id)
    assert stored is not None
    worker = MaintenanceWorker(pipeline.engines)
    result = worker._expand_durable_parse_frontier(
        SimpleNamespace(
            workspace_id=request.workspace_id,
            job_id="crash-window-job",
            maintenance_kind="document_parse_graph",
        ),
        stored[0],
        stored[1],
    )

    generation_store = ParseGenerationStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    generation_store.commit(
        ParseGeneration.model_validate(result["generation"]),
        ParseGenerationCommit.model_validate(result["commit"]),
        [ParseGenerationMember.model_validate(item) for item in result["members"]],
    )
    next_frontier = [ParseFrontierItem.model_validate(item) for item in result["frontier"]]
    pending_session = ParseSessionState.model_validate(result["session"]).model_copy(
        update={
            "frontier_ids": tuple(item.frontier_id for item in next_frontier),
            "consumed_frontier_ids": tuple(result["consumed_frontier_ids"]),
            "pending_view": result["parse_view"],
        }
    )
    session_store.save(pending_session, next_frontier, expected_version=stored[2])

    recovered, recovered_frontier, recovered_version = worker._recover_pending_parse_view(
        session_store,
        session_store.get(session.session_id),
    )
    assert recovered.pending_view is None
    assert recovered_frontier == next_frontier
    active = ParseViewResolver(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).resolve(source_document_id)
    assert active.view_id == ParseView.model_validate(result["parse_view"]).view_id
    assert active.member_ids == (result["members"][0]["member_id"],)

    pending_view = ParseView.model_validate(result["parse_view"])
    newer_view = pending_view.model_copy(
        update={
            "view_id": "newer-view-won",
            "view_version": pending_view.view_version + 1,
            "predecessor_view_id": pending_view.view_id,
        }
    )
    view_store = ParseViewStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    view_store.activate(newer_view, expected_view_version=pending_view.view_version)
    session_store.save(
        recovered.model_copy(update={"pending_view": pending_view.model_dump(mode="json")}),
        recovered_frontier,
        expected_version=recovered_version,
    )
    converged, _, _ = worker._recover_pending_parse_view(
        session_store,
        session_store.get(session.session_id),
    )
    assert converged.pending_view is None
    assert view_store.get(source_document_id).view_id == "newer-view-won"


def test_durable_expansion_segments_oversized_regions_before_parsing(
    pipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    request = ingest_request.model_copy(
        update={
            "operation_mode": "maintenance_first",
            "raw_text": "alpha " * 40,
            "parser_lane": "page_index",
        }
    )
    source_document_id = pipeline._source_document_id(request)
    namespaces = WorkspaceNamespaces(request.workspace_id)
    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=namespaces.conv_bg,
    )
    revision = pipeline.source_revision(request=request, source_document_id=source_document_id)
    session = pipeline.initialize_durable_parse_session(
        request=request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id or source_document_id,
        revision=revision,
        max_depth=3,
        max_region_chars=32,
        max_parser_calls=7,
        token_budget=8,
        wall_time_seconds=30.0,
    )
    assert session.max_parser_calls == 7
    assert session.max_region_chars == 32
    assert session.token_budget == 8
    assert session.wall_time_seconds == 30.0
    stored = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).get(session.session_id)
    assert stored is not None

    worker = MaintenanceWorker(pipeline.engines)
    result = worker._expand_durable_parse_frontier(
        SimpleNamespace(
            workspace_id=request.workspace_id,
            job_id="segment-job",
            maintenance_kind="document_expand_parse_children",
        ),
        stored[0],
        stored[1],
    )

    assert result["stable"] is False
    regions = [item["region"] for item in result["frontier"]]
    assert len(regions) == 2
    assert regions[0]["start_char"] == 0
    assert regions[0]["end_char"] == regions[1]["start_char"]
    assert regions[1]["end_char"] == len(request.raw_text)


def test_durable_expansion_retains_siblings_until_the_final_view_activation(
    pipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    request = ingest_request.model_copy(
        update={
            "operation_mode": "maintenance_first",
            "raw_text": "First bounded region. Second bounded region.",
            "parser_lane": "page_index",
        }
    )
    source_document_id = pipeline._source_document_id(request)
    namespaces = WorkspaceNamespaces(request.workspace_id)
    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=namespaces.conv_bg,
    )
    revision = pipeline.source_revision(request=request, source_document_id=source_document_id)
    session = pipeline.initialize_durable_parse_session(
        request=request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id or source_document_id,
        revision=revision,
    )
    session_store = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    stored = session_store.get(session.session_id)
    assert stored is not None
    initial = stored[1][0]
    split_at = len(request.raw_text) // 2
    first = initial.model_copy(
        update={
            "region": SourceRegion(
                source_document_id=initial.revision_document_id,
                start_char=0,
                end_char=split_at,
            )
        }
    )
    second = initial.model_copy(
        update={
            "frontier_id": "second-frontier",
            "region": SourceRegion(
                source_document_id=initial.revision_document_id,
                start_char=split_at,
                end_char=len(request.raw_text),
            ),
            "ordinal": 1,
        }
    )
    seeded_session = stored[0].model_copy(
        update={"frontier_ids": (first.frontier_id, second.frontier_id)}
    )
    session_store.save(seeded_session, [first, second], expected_version=stored[2])
    worker = MaintenanceWorker(pipeline.engines)
    ctx = SimpleNamespace(
        workspace_id=request.workspace_id,
        job_id="sibling-frontier-job",
        maintenance_kind="document_expand_parse_children",
    )

    first_result = worker._expand_durable_parse_frontier(ctx, seeded_session, [first, second])

    assert first_result["stable"] is False
    assert first_result["generation"]["status"] == "expanding"
    assert first_result["members"][0]["semantic_fingerprint"]
    assert [item["frontier_id"] for item in first_result["frontier"]] == [second.frontier_id]
    assert "parse_view" not in first_result

    generation_store = ParseGenerationStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    generation_store.commit(
        ParseGeneration.model_validate(first_result["generation"]),
        ParseGenerationCommit.model_validate(first_result["commit"]),
        [ParseGenerationMember.model_validate(item) for item in first_result["members"]],
    )
    next_frontier = [ParseFrontierItem.model_validate(item) for item in first_result["frontier"]]
    next_session = ParseSessionState.model_validate(first_result["session"]).model_copy(
        update={
            "frontier_ids": tuple(item.frontier_id for item in next_frontier),
            "consumed_frontier_ids": tuple(first_result["consumed_frontier_ids"]),
        }
    )
    session_store.save(next_session, next_frontier, expected_version=stored[2] + 1)

    second_result = worker._expand_durable_parse_frontier(ctx, next_session, next_frontier)

    assert second_result["stable"] is True
    assert second_result["generation"]["status"] == "stable"
    assert len(second_result["parse_view"]["selections"]) == 2
    assert {item["region"]["start_char"] for item in second_result["parse_view"]["selections"]} == {
        first.region.start_char,
        second.region.start_char,
    }


def test_targeted_reparse_rejects_partial_active_member_replacement() -> None:
    view = ParseView(
        view_id="view-1",
        view_version=1,
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        revision_document_id="revision-doc",
        selections=(
            ParseViewSelection(
                member_id="member-a",
                generation_id="generation-1",
                region=SourceRegion(
                    source_document_id="revision-doc",
                    start_char=0,
                    end_char=20,
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="cover every overlapping active generation member"):
        MaintenanceWorker._validate_reparse_region_coverage(
            view,
            SourceRegion(source_document_id="revision-doc", start_char=5, end_char=15),
        )


def test_invariant_safe_reparse_acceptance_scenario_covers_nineteen_steps(
    pipeline,
    ingest_request: IngestPipelineRequest,
) -> None:
    """Exercise the provider-free delayed parse, reparse, and recovery contract."""

    steps: list[str] = []
    request = ingest_request.model_copy(
        update={
            "operation_mode": "maintenance_first",
            "raw_text": "Region A remains authoritative. Region B needs review.",
            "parser_lane": "page_index",
        }
    )
    source_document_id = pipeline._source_document_id(request)
    namespaces = WorkspaceNamespaces(request.workspace_id)
    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=namespaces.conv_bg,
    )
    steps.append("source_registered")
    revision = pipeline.source_revision(request=request, source_document_id=source_document_id)
    assert revision.revision_document_id is not None
    steps.append("revision_captured")
    session = pipeline.initialize_durable_parse_session(
        request=request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id,
        revision=revision,
    )
    steps.append("session_seeded")
    session_store = ParseSessionStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    stored = session_store.get(session.session_id)
    assert stored is not None
    assert ParseViewResolver(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).resolve(source_document_id).is_legacy
    steps.append("legacy_view_until_parse_complete")

    initial = stored[1][0]
    split_at = len(request.raw_text) // 2
    first = initial.model_copy(
        update={
            "region": SourceRegion(
                source_document_id=initial.revision_document_id,
                start_char=0,
                end_char=split_at,
            )
        }
    )
    second = initial.model_copy(
        update={
            "frontier_id": "acceptance-second-frontier",
            "region": SourceRegion(
                source_document_id=initial.revision_document_id,
                start_char=split_at,
                end_char=len(request.raw_text),
            ),
            "ordinal": 1,
        }
    )
    seeded_session = stored[0].model_copy(
        update={"frontier_ids": (first.frontier_id, second.frontier_id)}
    )
    session_store.save(seeded_session, [first, second], expected_version=stored[2])
    steps.append("bounded_frontier_persisted")

    worker = MaintenanceWorker(pipeline.engines)
    context = SimpleNamespace(
        workspace_id=request.workspace_id,
        job_id="acceptance-expansion-a",
        maintenance_kind="document_expand_parse_children",
    )
    first_result = worker._expand_durable_parse_frontier(
        context,
        seeded_session,
        [first, second],
    )
    assert first_result["stable"] is False
    assert first_result["generation"]["status"] == "expanding"
    steps.append("first_bounded_expansion")
    generation_store = ParseGenerationStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    )
    generation_store.commit(
        ParseGeneration.model_validate(first_result["generation"]),
        ParseGenerationCommit.model_validate(first_result["commit"]),
        [ParseGenerationMember.model_validate(item) for item in first_result["members"]],
    )
    steps.append("first_generation_committed")
    resumed_frontier = [
        ParseFrontierItem.model_validate(item) for item in first_result["frontier"]
    ]
    resumed_session = ParseSessionState.model_validate(first_result["session"])
    assert resumed_frontier == [second]
    steps.append("worker_restart_resume_state_loaded")

    duplicate = worker._expand_durable_parse_frontier(
        context,
        seeded_session,
        [first, second],
    )
    assert duplicate["commit"]["commit_id"] == first_result["commit"]["commit_id"]
    steps.append("duplicate_delivery_reused_event_identity")
    second_result = worker._expand_durable_parse_frontier(
        SimpleNamespace(
            workspace_id=request.workspace_id,
            job_id="acceptance-expansion-b",
            maintenance_kind="document_expand_parse_children",
        ),
        resumed_session,
        resumed_frontier,
    )
    assert second_result["stable"] is True
    assert second_result["generation"]["status"] == "stable"
    generation_store.commit(
        ParseGeneration.model_validate(second_result["generation"]),
        ParseGenerationCommit.model_validate(second_result["commit"]),
        [ParseGenerationMember.model_validate(item) for item in second_result["members"]],
    )
    steps.append("final_frontier_committed")

    initial_view = ParseView.model_validate(second_result["parse_view"])
    ParseViewStore(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).activate(initial_view, expected_view_version=None)
    steps.append("stable_view_activated")
    resolved = ParseViewResolver(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).resolve(source_document_id)
    assert resolved.is_legacy is False
    assert len(resolved.member_ids) == 2
    steps.append("active_view_resolved")
    historical = generation_store.get(session.generation_id)
    assert historical is not None
    assert historical[0].status.name == "STABLE"
    steps.append("historical_generation_retained")

    reparse_session = pipeline.initialize_durable_parse_session(
        request=request,
        source_document_id=source_document_id,
        revision_document_id=revision.revision_document_id,
        revision=revision,
        parser_profile="page-index-model-upgrade",
        model_version="model-v2",
        initial_region=first.region,
    )
    steps.append("targeted_model_upgrade_seeded")
    reparse_stored = session_store.get(reparse_session.session_id)
    assert reparse_stored is not None
    reparse_result = worker._expand_durable_parse_frontier(
        SimpleNamespace(
            workspace_id=request.workspace_id,
            job_id="acceptance-reparse",
            maintenance_kind="document_reparse_region",
        ),
        reparse_stored[0],
        reparse_stored[1],
    )
    assert reparse_result["reconciliation"]["outcome"] == "equivalent"
    steps.append("targeted_region_reparsed")
    replacement_generation = ParseGeneration.model_validate(reparse_result["generation"])
    replacement_commit = ParseGenerationCommit.model_validate(reparse_result["commit"])
    replacement_members = [
        ParseGenerationMember.model_validate(item) for item in reparse_result["members"]
    ]
    generation_store.commit(replacement_generation, replacement_commit, replacement_members)
    steps.append("new_generation_committed")
    assert generation_store.get(session.generation_id) is not None
    steps.append("old_generation_still_readable")

    pending = ParseSessionState.model_validate(reparse_result["session"]).model_copy(
        update={
            "phase": ParseSessionPhase.STABLE,
            "frontier_ids": (),
            "pending_view": reparse_result["parse_view"],
        }
    )
    pending_version = session_store.get(reparse_session.session_id)
    assert pending_version is not None
    session_store.save(
        pending,
        [],
        expected_version=pending_version[2],
    )
    recovered, recovered_frontier, _ = worker._recover_pending_parse_view(
        session_store,
        session_store.get(reparse_session.session_id),
    )
    assert not recovered_frontier
    assert recovered.pending_view is None
    steps.append("crash_recovered_before_view_ack")
    final_view = ParseViewResolver(
        pipeline.engines.conversation.meta_sqlite,
        workspace_id=request.workspace_id,
    ).resolve(source_document_id)
    assert final_view.view_version == 2
    assert replacement_members[0].member_id in final_view.member_ids
    steps.append("repaired_view_is_active")
    assert len(steps) == 19
