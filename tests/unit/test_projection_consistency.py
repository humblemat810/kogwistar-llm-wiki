from __future__ import annotations

import json

from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.projection_worker import ProjectionWorker


def _job_field(job, name: str):
    if isinstance(job, dict):
        return job.get(name)
    return getattr(job, name, None)


def _job_payload(job) -> dict:
    payload = _job_field(job, "payload_json")
    if isinstance(payload, str) and payload:
        return json.loads(payload)
    return {}


def _sync_request(request):
    return request.model_copy(update={"promotion_mode": "sync"})


def test_projection_jobs_are_enqueued_per_sync_promotion(pipeline, ingest_request):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)

    promoted_ids = set()
    for index in range(3):
        request = _sync_request(
            ingest_request.model_copy(
                update={
                    "title": f"Doc {index}",
                    "source_uri": f"file:///contracts/doc-{index}.txt",
                }
            )
        )
        artifacts = pipeline.run(request)
        assert artifacts.promoted_entity_id is not None
        promoted_ids.add(artifacts.promoted_entity_id)

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        limit=20,
    )
    assert len(jobs) == 3
    assert {str(_job_field(job, "entity_kind")) for job in jobs} == {"projection_request"}
    assert {str(_job_field(job, "namespace")) for job in jobs} == {ns.projection_jobs}
    assert {str(_job_field(job, "entity_id")) for job in jobs} == promoted_ids


def test_projection_worker_processes_durable_jobs_and_records_manifest(
    pipeline, ingest_request, tmp_path
):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    vault_root = tmp_path / "obsidian_vault"
    vault_root.mkdir()

    artifacts = pipeline.run(_sync_request(ingest_request))
    assert artifacts.promoted_entity_id is not None

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        limit=10,
    )
    assert len(jobs) == 1
    job = jobs[0]
    payload = _job_payload(job)
    assert payload["promoted_entity_id"] == artifacts.promoted_entity_id

    worker = ProjectionWorker(pipeline.engines)
    worker.process_pending_projections(workspace_id, str(vault_root))

    pending_jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        status="PENDING",
        limit=10,
    )
    done_jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        status="DONE",
        limit=10,
    )
    assert pending_jobs == []
    assert len(done_jobs) == 1
    assert _job_field(done_jobs[0], "job_id") == _job_field(job, "job_id")

    manifest = pipeline.engines.conversation.meta_sqlite.get_named_projection(
        ns.projection_manifest,
        workspace_id,
    )
    assert manifest is not None
    assert manifest["namespace"] == ns.projection_manifest
    assert manifest["key"] == workspace_id
    assert manifest["materialization_status"] == "ready"
    assert manifest["payload"]["status"] == "ready"
    assert artifacts.promoted_entity_id in manifest["payload"]["projected_ids"]
    assert list(vault_root.rglob("*.md")), "Projection worker should write markdown files"


def test_projection_manager_reads_manifest_written_by_worker(
    pipeline, ingest_request, tmp_path
):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    vault_root = tmp_path / "manifest_vault"
    vault_root.mkdir()

    pipeline.run(_sync_request(ingest_request))
    worker = ProjectionWorker(pipeline.engines)
    worker.process_pending_projections(workspace_id, str(vault_root))

    manifest = pipeline.engines.conversation.meta_sqlite.get_named_projection(
        ns.projection_manifest,
        workspace_id,
    )
    assert manifest is not None

    snapshot = pipeline.build_projection_snapshot(workspace_id=workspace_id)
    titles = {entity.title for entity in snapshot.entities}
    assert ingest_request.title in titles
    assert manifest["namespace"] == ns.projection_manifest
    assert manifest["key"] == workspace_id


def test_projection_worker_is_noop_for_empty_queue(pipeline, tmp_path):
    workspace_id = "empty-workspace"
    vault_root = tmp_path / "empty_vault"
    vault_root.mkdir()

    worker = ProjectionWorker(pipeline.engines)
    worker.process_pending_projections(workspace_id, str(vault_root))

    ns = WorkspaceNamespaces(workspace_id)
    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        limit=10,
    )
    assert jobs == []
