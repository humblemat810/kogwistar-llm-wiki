from __future__ import annotations

import json
from unittest.mock import Mock

from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


def _job_field(job, name: str):
    if isinstance(job, dict):
        return job.get(name)
    return getattr(job, name, None)


def _job_payload(job) -> dict:
    payload = _job_field(job, "payload_json")
    if isinstance(payload, str) and payload:
        return json.loads(payload)
    return {}


def test_conversation_lanes_share_same_engine(pipeline):
    assert pipeline.engines.conversation is pipeline.engines.conversation


def test_pipeline_invokes_parser_and_persists_result(pipeline, ingest_request):
    parser = Mock(wraps=pipeline.parser)
    pipeline.parser = parser
    artifacts = pipeline.run(ingest_request)
    parser.assert_called_once()
    assert artifacts.source_document_id


def test_pipeline_enqueues_maintenance_job_in_durable_store(pipeline, ingest_request):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)

    pipeline.run(ingest_request)

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=10,
    )
    assert len(jobs) == 1
    job = jobs[0]
    assert _job_field(job, "namespace") == ns.maintenance_jobs
    assert _job_field(job, "entity_kind") == "maintenance_job"
    assert _job_field(job, "entity_id") == pipeline._source_document_id(ingest_request)
    payload = _job_payload(job)
    assert payload["workspace_id"] == workspace_id
    assert payload["request_node_id"] == _job_field(job, "job_id")
    assert payload["source_document_id"] == pipeline._source_document_id(ingest_request)


def test_pipeline_enqueues_projection_job_only_for_sync_promotion(pipeline, ingest_request):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})

    artifacts = pipeline.run(sync_request)

    jobs = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.projection_jobs,
        limit=10,
    )
    assert len(jobs) == 1
    job = jobs[0]
    assert _job_field(job, "namespace") == ns.projection_jobs
    assert _job_field(job, "entity_kind") == "projection_request"
    assert _job_field(job, "entity_id") == artifacts.promoted_entity_id
    payload = _job_payload(job)
    assert payload["workspace_id"] == workspace_id
    assert payload["promoted_entity_id"] == artifacts.promoted_entity_id
    assert payload["promotion_mode"] == "sync"
