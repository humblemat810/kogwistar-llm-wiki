from __future__ import annotations

from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.ingest_pipeline import IngestPipelineRequest
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.parse_generation_store import ParseGenerationStore
from kogwistar_llm_wiki.parse_session_store import ParseSessionStore
from kogwistar_llm_wiki.parse_views import (
    ParseFrontierItem,
    ParseGeneration,
    ParseGenerationCommit,
    ParseGenerationMember,
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
