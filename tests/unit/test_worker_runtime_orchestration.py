from __future__ import annotations

import pytest
from pathlib import Path
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline, IngestPipelineRequest
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


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
    
    # 4. Verify Final State - Request marked completed
    updated_requests = pipeline.engines.conversation.read.get_nodes(
        ids=[str(req_node.id)]
    )
    assert len(updated_requests) > 0, f"Request {req_node.id} not found after worker run"
    assert updated_requests[0].metadata.get("status") == "completed"
    run_id = updated_requests[0].metadata.get("run_id")
    assert run_id is not None

    # 5. Verify Graph-Native Trace (recorded in conversation engine)
    # The runtime creates WorkflowRunNode and WorkflowStepExecNode
    traces = pipeline.engines.conversation.read.get_nodes(
        where={
            "run_id": run_id,
        }
    )
    # Should find at least the Run node and the Step exec node
    kinds = [t.metadata.get("entity_type") for t in traces]
    assert "workflow_run" in kinds
    assert "workflow_step_exec" in kinds
    
    # Verify the steps were 'distill' and 'check_done'
    node_types = [t.metadata.get("op") for t in traces if t.metadata.get("entity_type") == "workflow_step_exec"]
    assert "distill" in node_types
    assert "check_done" in node_types
