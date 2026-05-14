from __future__ import annotations

import json

import pytest

from kogwistar.engine_core.models import Edge, Grounding, Span
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.projection_worker import ProjectionWorker
from kogwistar_llm_wiki.utils import _temporary_namespace


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


def _cross_workspace_edge(*, source_id: str, target_id: str, relation: str) -> Edge:
    return Edge(
        id=f"edge|{source_id}|{target_id}|{relation}",
        label=relation,
        type="relationship",
        doc_id=f"edge|{source_id}|{target_id}|{relation}",
        summary=f"{source_id} -> {target_id} ({relation})",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation()])],
        properties={},
        source_ids=[source_id],
        target_ids=[target_id],
        relation=relation,
        source_edge_ids=[],
        target_edge_ids=[],
        embedding=None,
        metadata={},
        domain_id=None,
        canonical_entity_id=None,
    )


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


def test_repeated_sync_ingest_reuses_projection_job_for_same_source(pipeline, ingest_request):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    request = _sync_request(ingest_request)

    first = pipeline.run(request)
    second = pipeline.run(request)

    assert second.promoted_entity_id == first.promoted_entity_id
    assert second.maintenance_job_id == first.maintenance_job_id

    maintenance_jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=20,
    )
    assert len(maintenance_jobs) == 1
    assert _job_payload(maintenance_jobs[0])["request_node_id"] == first.maintenance_job_id

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        maintenance_requests = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "lane_message",
                "msg_type": "request.maintenance",
            },
        )
    assert len(maintenance_requests) == 1

    projection_jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        limit=20,
    )
    assert len(projection_jobs) == 1
    assert _job_payload(projection_jobs[0])["promoted_entity_id"] == first.promoted_entity_id


def test_duplicate_sync_ingest_repairs_missing_maintenance_job_after_lane_request(
    pipeline,
    ingest_request,
    monkeypatch,
):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    request = _sync_request(ingest_request)
    original_enqueue = pipeline._enqueue_maintenance_job
    skipped = {"count": 0}

    def flaky_enqueue(**kwargs):
        if skipped["count"] == 0:
            skipped["count"] += 1
            return str(kwargs["request_node_id"])
        return original_enqueue(**kwargs)

    monkeypatch.setattr(pipeline, "_enqueue_maintenance_job", flaky_enqueue)

    first = pipeline.run(request)
    assert skipped["count"] == 1
    assert first.maintenance_job_id is not None

    jobs_after_crash = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=20,
    )
    assert jobs_after_crash == []
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        lane_requests = pipeline.engines.conversation.read.get_nodes(
            where={
                "artifact_kind": "lane_message",
                "msg_type": "request.maintenance",
            },
        )
    assert len(lane_requests) == 1
    lane_message_id = str(lane_requests[0].id)

    second = pipeline.run(request)

    assert second.maintenance_job_id == first.maintenance_job_id
    jobs_after_repair = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=20,
    )
    assert len(jobs_after_repair) == 1
    repaired_payload = _job_payload(jobs_after_repair[0])
    assert repaired_payload["request_node_id"] == first.maintenance_job_id
    assert repaired_payload["lane_message_id"] == lane_message_id


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
    assert manifest["payload"]["ready_projected_ids"] == manifest["payload"]["projected_ids"]
    assert artifacts.promoted_entity_id in manifest["payload"]["ready_projected_ids"]
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


def test_projection_snapshot_excludes_cross_workspace_edges(pipeline, ingest_request):
    workspace_id = "projection_workspace_a"
    foreign_workspace_id = "projection_workspace_b"

    local_artifacts = pipeline.run(
        _sync_request(
            ingest_request.model_copy(
                update={
                    "workspace_id": workspace_id,
                    "title": "Projection Local",
                    "source_uri": "file:///projection/local.txt",
                }
            )
        )
    )
    foreign_artifacts = pipeline.run(
        _sync_request(
            ingest_request.model_copy(
                update={
                    "workspace_id": foreign_workspace_id,
                    "title": "Projection Foreign",
                    "source_uri": "file:///projection/foreign.txt",
                }
            )
        )
    )

    assert local_artifacts.promoted_entity_id is not None
    assert foreign_artifacts.promoted_entity_id is not None

    pipeline.engines.kg.write.add_edge(
        _cross_workspace_edge(
            source_id=str(local_artifacts.promoted_entity_id),
            target_id=str(foreign_artifacts.promoted_entity_id),
            relation="cross_workspace",
        )
    )

    snapshot = pipeline.build_projection_snapshot(workspace_id=workspace_id)
    local_entity = next(
        entity for entity in snapshot.entities if entity.kg_id == str(local_artifacts.promoted_entity_id)
    )

    assert "Projection Local" in {entity.title for entity in snapshot.entities}
    assert "Projection Foreign" not in {entity.title for entity in snapshot.entities}
    assert str(foreign_artifacts.promoted_entity_id) not in local_entity.target_ids
    assert all(rel.relation_type != "cross_workspace" for rel in local_entity.relationships)


def test_failed_projection_does_not_mark_manifest_id_ready(pipeline, ingest_request, tmp_path, monkeypatch):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    vault_root = tmp_path / "manifest_failure_vault"
    vault_root.mkdir()

    artifacts = pipeline.run(_sync_request(ingest_request))
    worker = ProjectionWorker(pipeline.engines)
    monkeypatch.setattr(worker.manager, "sync_obsidian_vault", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("sink boom")))

    with pytest.raises(RuntimeError, match="sink boom"):
        worker.process_pending_projections(workspace_id, str(vault_root))

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        limit=10,
    )
    assert jobs
    assert _job_field(jobs[0], "status") != "DOING"
    assert int(_job_field(jobs[0], "retry_count") or 0) == 1

    manifest = pipeline.engines.conversation.meta_sqlite.get_named_projection(
        ns.projection_manifest,
        workspace_id,
    )
    assert manifest is not None
    payload = manifest["payload"]
    assert payload["status"] == "failed"
    assert artifacts.promoted_entity_id in payload["desired_projected_ids"]
    assert artifacts.promoted_entity_id in payload["failed_projected_ids"]
    assert artifacts.promoted_entity_id not in payload["ready_projected_ids"]
    assert artifacts.promoted_entity_id not in payload["projected_ids"]
    assert payload["ready_projected_ids"] == payload["projected_ids"]


def test_projection_snapshot_reads_legacy_projection_manifest_projected_ids_only(
    pipeline,
    ingest_request,
):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)

    artifacts = pipeline.run(_sync_request(ingest_request))
    assert artifacts.promoted_entity_id is not None

    pipeline.engines.conversation.meta_sqlite.replace_named_projection(
        namespace=ns.projection_manifest,
        key=workspace_id,
        payload={
            "workspace_id": workspace_id,
            "projected_ids": [str(artifacts.promoted_entity_id)],
            "status": "ready",
        },
        last_authoritative_seq=1,
        last_materialized_seq=1,
        projection_schema_version=1,
        materialization_status="ready",
    )

    snapshot = pipeline.build_projection_snapshot(workspace_id=workspace_id)
    assert {entity.kg_id for entity in snapshot.entities} == {str(artifacts.promoted_entity_id)}


def test_projection_snapshot_prefers_ready_projected_ids_over_failed_ids(
    pipeline,
    ingest_request,
):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)

    ready_artifacts = pipeline.run(
        _sync_request(
            ingest_request.model_copy(
                update={
                    "source_uri": "file:///contracts/ready.txt",
                    "title": "Ready Contract",
                }
            )
        )
    )
    failed_artifacts = pipeline.run(
        _sync_request(
            ingest_request.model_copy(
                update={
                    "source_uri": "file:///contracts/failed.txt",
                    "title": "Failed Contract",
                }
            )
        )
    )

    assert ready_artifacts.promoted_entity_id is not None
    assert failed_artifacts.promoted_entity_id is not None

    pipeline.engines.conversation.meta_sqlite.replace_named_projection(
        namespace=ns.projection_manifest,
        key=workspace_id,
        payload={
            "workspace_id": workspace_id,
            "desired_projected_ids": [
                str(ready_artifacts.promoted_entity_id),
                str(failed_artifacts.promoted_entity_id),
            ],
            "ready_projected_ids": [str(ready_artifacts.promoted_entity_id)],
            "failed_projected_ids": [str(failed_artifacts.promoted_entity_id)],
            "projected_ids": [str(ready_artifacts.promoted_entity_id)],
            "status": "failed",
        },
        last_authoritative_seq=1,
        last_materialized_seq=1,
        projection_schema_version=1,
        materialization_status="failed",
    )

    snapshot = pipeline.build_projection_snapshot(workspace_id=workspace_id)
    snapshot_ids = {entity.kg_id for entity in snapshot.entities}
    assert snapshot_ids == {str(ready_artifacts.promoted_entity_id)}


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
