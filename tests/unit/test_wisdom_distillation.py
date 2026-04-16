from __future__ import annotations

import pytest
from pydantic import ValidationError

from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.utils import _temporary_namespace


def _sync_request(request, **updates):
    return request.model_copy(update={"promotion_mode": "sync", **updates})


def _run_sync_ingest(pipeline, request):
    artifacts = pipeline.run(request)
    assert artifacts.maintenance_job_id
    assert artifacts.promoted_entity_id is not None
    return artifacts


def test_wisdom_distillation_multi_document_grounding(pipeline, ingest_request):
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

    with _temporary_namespace(engines.wisdom, ns.wisdom):
        wisdom_nodes = engines.wisdom.read.get_nodes(
            where={"artifact_kind": "wisdom", "workspace_id": workspace_id}
        )

    assert len(wisdom_nodes) == 1
    wisdom = wisdom_nodes[0]
    assert "Shared Entity" in wisdom.label
    assert len(wisdom.mentions) >= 1

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


def test_wisdom_distillation_no_knowledge_noop(pipeline, ingest_request):
    workspace_id = "empty_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs(workspace_id)

    with _temporary_namespace(engines.wisdom, ns.wisdom):
        wisdom_nodes = engines.wisdom.read.get_nodes(
            where={"workspace_id": workspace_id}
        )
    assert len(wisdom_nodes) == 0


def test_wisdom_distillation_error_resilience(pipeline, ingest_request, monkeypatch):
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


def test_wisdom_distillation_pydantic_validation(pipeline):
    """Negative test: Ensure Span validation fails with incomplete data."""
    from kogwistar.engine_core.models import Span

    with pytest.raises(ValidationError):
        Span(
            doc_id="test",
            start_char=0,
            end_char=10,
            excerpt="test",
        )


def test_wisdom_distillation_eager_mode_manual_trigger(pipeline, ingest_request):
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
