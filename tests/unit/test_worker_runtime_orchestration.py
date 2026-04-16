from __future__ import annotations

import pytest
from pathlib import Path
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline, IngestPipelineRequest
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def test_maintenance_flow_records_graph_native_trace(pipeline: IngestPipeline, ingest_request: IngestPipelineRequest):
    # 1. Setup - Materialize design
    materialize_maintenance_designs(pipeline.engines.workflow)
    
    # 2. Trigger Ingest - Creates maintenance request in conversation engine (conv_bg)
    sync_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    artifacts = pipeline.run(sync_request)
    
    workspace_id = sync_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    
    # Verify request existence in conversation engine
    requests = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "namespace": ns.conv_bg,
        }
    )
    assert len(requests) == 1
    req_node = requests[0]
    assert req_node.metadata.get("status") == "pending"

    # 3. Run Worker
    worker = MaintenanceWorker(pipeline.engines)
    worker.process_pending_jobs(workspace_id)
    
    # 4. Verify Final State - WorkflowRunNode Trace
    # We no longer perform CRUD updates on the request node itself.
    # The authoritative state is in the append-only traces.
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        runs = pipeline.engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(req_node.id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) == 1, f"No workflow_run found for request {req_node.id}"
        run_id = runs[0].metadata.get("run_id")
        assert run_id is not None
        
        # Verify authoritative completion event existence
        completes = pipeline.engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_completed"
            }
        )
        assert len(completes) == 1, f"No workflow_completed found for run {run_id}"

    # 5. Verify Graph-Native Trace Details
    # The runtime creates WorkflowRunNode and WorkflowStepExecNode
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        traces = pipeline.engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
            }
        )
    # Should find at least the Run node and the Step exec nodes
    kinds = [t.metadata.get("entity_type") for t in traces]
    assert "workflow_run" in kinds
    assert "workflow_step_exec" in kinds
    
    # Verify the steps were 'distill' and 'check_done'
    node_ops = [t.metadata.get("op") for t in traces if t.metadata.get("entity_type") == "workflow_step_exec"]
    assert "distill" in node_ops
    assert "check_done" in node_ops
