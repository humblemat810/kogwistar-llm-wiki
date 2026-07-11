from kogwistar_llm_wiki.maintenance_guards import (
    build_source_revision,
    evaluate_maintenance_guard,
)
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker


def test_ready_guard_requires_matching_revision_digest_and_stage():
    revision = build_source_revision(
        workspace_id="ws",
        source_document_id="doc-1",
        raw_text="v1",
        attempt_id="attempt-1",
    )

    decision = evaluate_maintenance_guard(
        source_revision=revision,
        requested_revision_id=revision.revision_id,
        requested_digest=revision.source_digest,
        required_stage="parsed_graph_persisted",
        ready_revision_ids={revision.revision_id},
    )

    assert decision.status == "ready"
    assert decision.reason == "source_revision_and_readiness_verified"


def test_guard_rejects_stale_revision_instead_of_running_old_work():
    current = build_source_revision(
        workspace_id="ws",
        source_document_id="doc-1",
        raw_text="v2",
        attempt_id="attempt-2",
    )
    old = build_source_revision(
        workspace_id="ws",
        source_document_id="doc-1",
        raw_text="v1",
        attempt_id="attempt-1",
    )

    decision = evaluate_maintenance_guard(
        source_revision=current,
        requested_revision_id=old.revision_id,
        requested_digest=old.source_digest,
        required_stage="parsed_graph_persisted",
        ready_revision_ids={old.revision_id},
    )

    assert decision.status == "stale"
    assert decision.reason == "source_revision_mismatch"


def test_guard_blocks_ready_revision_until_required_stage_exists():
    revision = build_source_revision(
        workspace_id="ws",
        source_document_id="doc-1",
        raw_text="v1",
        attempt_id="attempt-1",
    )

    decision = evaluate_maintenance_guard(
        source_revision=revision,
        requested_revision_id=revision.revision_id,
        requested_digest=revision.source_digest,
        required_stage="parsed_graph_persisted",
        ready_revision_ids=set(),
    )

    assert decision.status == "blocked"
    assert decision.reason == "required_stage_missing:parsed_graph_persisted"


def test_worker_fails_closed_when_seed_job_has_no_source_map_readiness(
    pipeline,
    ingest_request,
):
    source_document_id = pipeline._source_document_id(ingest_request)
    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=ns.conv_fg,
    )
    job_id = pipeline.create_maintenance_request(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=ns.conv_bg,
        maintenance_kind="document_seed_graph",
    )

    MaintenanceWorker(pipeline.engines).process_pending_jobs(ingest_request.workspace_id)

    jobs = pipeline.engines.conversation.jobs.list(
        namespace=ns.maintenance_jobs,
        limit=10,
    )
    job = next(item for item in jobs if item.job_id == job_id)
    assert job.last_error is not None
    assert "required_stage_missing:source_map_seeded" in job.last_error
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        artifacts = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "maintenance_guard_decision",
                "job_id": job_id,
            }
        )
    assert artifacts


def test_source_revision_rehydrates_after_pipeline_cache_loss(pipeline, ingest_request):
    source_document_id = pipeline._source_document_id(ingest_request)
    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    pipeline.register_source(
        request=ingest_request,
        source_document_id=source_document_id,
        namespace=ns.conv_fg,
    )
    original = pipeline.source_revision(
        request=ingest_request,
        source_document_id=source_document_id,
    )
    pipeline._source_revisions.clear()

    restored = pipeline.source_revision(
        request=ingest_request,
        source_document_id=source_document_id,
    )

    assert restored == original
