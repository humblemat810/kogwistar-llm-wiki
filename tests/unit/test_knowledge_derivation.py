from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.conftest import _build_engine

from kogwistar_llm_wiki import IngestPipeline, NamespaceEngines
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.utils import _temporary_namespace
import json


def _sync_request(request, **updates):
    return request.model_copy(update={"promotion_mode": "sync", **updates})


def _run_sync_ingest(pipeline, request):
    artifacts = pipeline.run(request)
    assert artifacts.maintenance_job_id
    assert artifacts.promoted_entity_id is not None
    return artifacts


def test_knowledge_derivation_multi_document_grounding(pipeline, ingest_request):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines

    materialize_maintenance_designs(engines.workflow)

    req1 = _sync_request(
        ingest_request,
        title="Shared Entity",
        source_uri="file://doc_a.txt",
    )
    req2 = _sync_request(
        ingest_request,
        title="Shared Entity",
        source_uri="file://doc_b.txt",
    )

    art1 = _run_sync_ingest(pipeline, req1)
    art2 = _run_sync_ingest(pipeline, req2)

    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs(workspace_id)

    with _temporary_namespace(engines.kg, ns.derived_knowledge):
        derived_nodes = engines.kg.read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )

    assert len(derived_nodes) == 1
    derived = derived_nodes[0]
    assert "Shared Entity" in derived.label
    assert len(derived.mentions) >= 1
    assert derived.metadata.get("artifact_kind") == "derived_knowledge"
    assert derived.metadata.get("created_at_ms")
    assert derived.metadata.get("source_node_ids")
    assert derived.metadata.get("replaces_ids") is not None

    with _temporary_namespace(engines.conversation, ns.conv_bg):
        runs = engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(art1.maintenance_job_id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) >= 1
        run_ids = {str(run.metadata.get("run_id")) for run in runs if run.metadata.get("run_id")}
        assert run_ids
        step_execs = engines.conversation.read.get_nodes(
            where={
                "run_id": run_ids.pop(),
                "entity_type": "workflow_step_exec",
                "op": "distill",
            }
        )
        assert step_execs

    done_jobs = engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        status="DONE",
        limit=10,
    )
    assert len(done_jobs) >= 1
    assert {str(job.job_id) for job in done_jobs} >= {str(art1.maintenance_job_id), str(art2.maintenance_job_id)}


def test_same_engine_derived_knowledge_uses_namespace_isolation(pipeline, ingest_request):
    workspace_id = "same_engine_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    artifacts = _run_sync_ingest(
        pipeline,
        _sync_request(
            ingest_request,
            workspace_id=workspace_id,
            title="Same Engine Entity",
            source_uri="file://same_engine.txt",
        ),
    )
    assert artifacts.promoted_entity_id is not None

    MaintenanceWorker(engines).process_pending_jobs(workspace_id)

    with _temporary_namespace(engines.kg, ns.derived_knowledge):
        derived_nodes = engines.kg.read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )

    assert engines.derived_knowledge_engine() is engines.kg
    assert len(derived_nodes) == 1
    derived = derived_nodes[0]
    assert derived.label == "Same Engine Entity"
    assert derived.metadata.get("artifact_kind") == "derived_knowledge"
    assert derived.metadata.get("created_at_ms")
    assert derived.metadata.get("source_node_ids")
    assert derived.metadata.get("replaces_ids") is not None


def test_knowledge_derivation_no_knowledge_noop(pipeline, ingest_request):
    workspace_id = "empty_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs(workspace_id)

    with _temporary_namespace(engines.kg, ns.derived_knowledge):
        derived_nodes = engines.kg.read.get_nodes(
            where={"workspace_id": workspace_id}
        )
    assert len(derived_nodes) == 0


def test_knowledge_derivation_error_resilience(pipeline, ingest_request, monkeypatch):
    workspace_id = "error_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    artifacts = _run_sync_ingest(
        pipeline,
        _sync_request(ingest_request, workspace_id=workspace_id),
    )

    worker = MaintenanceWorker(engines)

    def mock_distill(*args, **kwargs):
        raise RuntimeError("Distillation Logic Crash")

    worker.resolver.register("distill")(mock_distill)

    worker.process_pending_jobs(workspace_id)

    with _temporary_namespace(engines.conversation, ns.conv_bg):
        runs = engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(artifacts.maintenance_job_id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) >= 1
        run_id = runs[0].metadata.get("run_id")

        steps = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_step_exec",
                "op": "distill",
            }
        )
        assert len(steps) >= 1
        assert steps[0].metadata.get("status") in ("failure", "error")


def test_execution_wisdom_derivation_uses_history_failures(pipeline, ingest_request):
    workspace_id = "history_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    first = _run_sync_ingest(
        pipeline,
        _sync_request(ingest_request, workspace_id=workspace_id, title="History A", source_uri="file://history_a.txt"),
    )
    second = _run_sync_ingest(
        pipeline,
        _sync_request(ingest_request, workspace_id=workspace_id, title="History B", source_uri="file://history_b.txt"),
    )

    failing_worker = MaintenanceWorker(engines)

    def mock_distill(*args, **kwargs):
        raise RuntimeError("history failure")

    failing_worker.resolver.register("distill")(mock_distill)
    failed_jobs = engines.conversation.meta_sqlite.claim_index_jobs(
        limit=10,
        lease_seconds=60,
        namespace=ns.maintenance_jobs,
    )
    assert len(failed_jobs) >= 2
    for job in failed_jobs[:2]:
        failing_worker._handle_job(workspace_id, job)

    third = _run_sync_ingest(
        pipeline,
        _sync_request(ingest_request, workspace_id=workspace_id, title="History C", source_uri="file://history_c.txt"),
    )

    successful_worker = MaintenanceWorker(engines)
    successful_worker.process_pending_jobs(workspace_id)

    wisdom_job_id = f"{third.maintenance_job_id}:execution_wisdom"
    engines.conversation.meta_sqlite.enqueue_index_job(
        job_id=wisdom_job_id,
        namespace=ns.maintenance_jobs,
        entity_kind="maintenance_job",
        entity_id=third.source_document_id,
        index_kind="maintenance_job",
        op="UPSERT",
        payload_json=json.dumps(
            {
                "workspace_id": workspace_id,
                "request_node_id": third.maintenance_job_id,
                "source_document_id": third.source_document_id,
                "maintenance_kind": "execution_wisdom",
            }
        ),
    )
    successful_worker.process_pending_jobs(workspace_id)

    with _temporary_namespace(engines.wisdom, ns.wisdom):
        execution_wisdom = engines.wisdom.read.get_nodes(
            where={
                "artifact_kind": "execution_wisdom",
                "workspace_id": workspace_id,
                "step_op": "distill",
            }
        )

    assert len(execution_wisdom) == 1
    wisdom = execution_wisdom[0]
    assert wisdom.metadata.get("failure_count", 0) >= 2
    assert "distill" in wisdom.label

    with _temporary_namespace(engines.conversation, ns.conv_bg):
        runs = engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(third.maintenance_job_id),
                "entity_type": "workflow_run",
            }
        )
        assert runs
        run_id = runs[0].metadata.get("run_id")
    queued_or_done = engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=20,
    )
    job_ids = {str(job.job_id) for job in queued_or_done}
    assert {
        str(first.maintenance_job_id),
        str(second.maintenance_job_id),
        str(third.maintenance_job_id),
        wisdom_job_id,
    } <= job_ids


def test_knowledge_derivation_pydantic_validation(pipeline):
    """Negative test: Ensure Span validation fails with incomplete data."""
    from kogwistar.engine_core.models import Span

    with pytest.raises(ValidationError):
        Span(
            doc_id="test",
            start_char=0,
            end_char=10,
            excerpt="test",
        )


def test_knowledge_derivation_eager_mode_manual_trigger(pipeline, ingest_request):
    """Verify that MaintenanceWorker can be initialized in eager mode."""
    engines = pipeline.engines
    worker = MaintenanceWorker(engines, eager_mode=True)
    assert worker.eager_mode is True

    workspace_id = "eager_test"
    materialize_maintenance_designs(engines.workflow)

    artifacts = _run_sync_ingest(
        pipeline,
        _sync_request(ingest_request, workspace_id=workspace_id),
    )

    worker.process_pending_jobs(workspace_id)

    ns = WorkspaceNamespaces(workspace_id)
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        runs = engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(artifacts.maintenance_job_id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) >= 1
        run_id = runs[0].metadata.get("run_id")

        completes = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_completed",
            }
        )
        assert len(completes) == 1

        steps = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_step_exec",
                "op": "distill",
                "status": "ok",
            }
        )
        assert len(steps) == 1


def test_knowledge_derivation_can_use_separate_engine(namespace_engines, ingest_request, tmp_path):
    workspace_id = "split_engine_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    split_derived_engine = _build_engine(tmp_path, kind="derived_knowledge")
    split_engines = NamespaceEngines(
        conversation=namespace_engines.conversation,
        workflow=namespace_engines.workflow,
        kg=namespace_engines.kg,
        wisdom=namespace_engines.wisdom,
        derived_knowledge=split_derived_engine,
    )
    pipeline = IngestPipeline(split_engines)
    materialize_maintenance_designs(split_engines.workflow)

    request = _sync_request(
        ingest_request,
        workspace_id=workspace_id,
        title="Split Engine Entity",
        source_uri="file://split_engine.txt",
    )
    artifacts = _run_sync_ingest(pipeline, request)
    assert artifacts.promoted_entity_id is not None

    MaintenanceWorker(split_engines).process_pending_jobs(workspace_id)

    with _temporary_namespace(split_engines.kg, ns.kg):
        raw_kg_nodes = split_engines.kg.read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )
    with _temporary_namespace(split_engines.derived_knowledge_engine(), ns.derived_knowledge):
        derived_nodes = split_engines.derived_knowledge_engine().read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )

    assert len(raw_kg_nodes) == 0
    assert len(derived_nodes) == 1
    assert derived_nodes[0].label == "Split Engine Entity"
