from __future__ import annotations

import pytest
from pydantic import ValidationError
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def test_wisdom_distillation_multi_document_grounding(pipeline, ingest_request):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    
    # 1. Setup: Materialize designs into workflow engine
    materialize_maintenance_designs(engines.workflow)
    
    # 2. Ingest 2 documents with the same entity name (by using same title)
    # We use promotion_mode='sync' to ensure they reach the KG
    req1 = ingest_request.model_copy(update={
        "title": "Shared Entity", "source_uri": "file://doc_a.txt", "promotion_mode": "sync"
    })
    req2 = ingest_request.model_copy(update={
        "title": "Shared Entity", "source_uri": "file://doc_b.txt", "promotion_mode": "sync"
    })
    
    pipeline.run(req1)
    pipeline.run(req2)
    
    # 3. Create a Maintenance Job Request manually
    # (In a real system, the IngestPipeline or a scheduler would do this by emitting an entity event)
    from kogwistar.engine_core.models import Node, Grounding, Span
    job_req = Node(
        label="Distill Wisdom (Test)",
        type="entity",
        summary="Triggering wisdom distillation",
        mentions=[Grounding(spans=[Span(doc_id="workflow", start_char=0, end_char=1, excerpt="trigger", document_page_url="dummy", collection_page_url="dummy", insertion_method="auto")])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
        }
    )
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(job_req)
        
    # 4. Run the Worker
    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs(workspace_id)
    
    # 5. Verify Wisdom Generation (Merging Logic)
    # Both Doc A and Doc B should have produced "promoted_knowledge" nodes.
    # By default, the IngestPipeline uses the document title as the label if no specialized entity extraction is active.
    # To test MERGING, we should have nodes with the SAME label in the KG.
    # Since our worker groups by `node.label`, we expect two wisdom nodes if labels differ.
    
    with _temporary_namespace(engines.wisdom, ns.wisdom):
        wisdom_nodes = engines.wisdom.read.get_nodes(
            where={"artifact_kind": "wisdom", "workspace_id": workspace_id}
        )
        
    assert len(wisdom_nodes) == 1
    wisdom = wisdom_nodes[0]
    assert "Shared Entity" in wisdom.label
    
    # Verify Grounding (Merged and Deduplicated Spans)
    # Both Doc A and Doc B had 1 mention each.
    # The distillation logic should aggregate them.
    assert len(wisdom.mentions) >= 1 
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        # 4. Verify Trace: WorkflowRunNode should exist
        runs = engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(job_req.id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) == 1
        run_id = runs[0].metadata.get("run_id")
        
        # Verify authoritative completion event existence
        completes = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_completed"
            }
        )
        assert len(completes) == 1
        
        # 5. Verify Steps: distill step should be ok
        steps = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_step_exec",
                "op": "distill",
                "status": "ok"
            }
        )
        assert len(steps) == 1


def test_wisdom_distillation_no_knowledge_noop(pipeline, ingest_request):
    workspace_id = "empty_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)
    
    # 1. Trigger job in empty workspace
    from kogwistar.engine_core.models import Node, Grounding, Span
    job_req = Node(
        label="Empty Distillation",
        type="entity",
        summary="Should do nothing",
        mentions=[Grounding(spans=[Span(doc_id="workflow", start_char=0, end_char=1, excerpt="noop", document_page_url="dummy", collection_page_url="dummy", insertion_method="auto")])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
        }
    )
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(job_req)
        
    # 2. Run Worker
    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs(workspace_id)
    
    # 3. Verify no wisdom produced
    with _temporary_namespace(engines.wisdom, ns.wisdom):
        wisdom_nodes = engines.wisdom.read.get_nodes(
            where={"metadata.workspace_id": workspace_id}
        )
    assert len(wisdom_nodes) == 0


def test_wisdom_distillation_error_resilience(pipeline, ingest_request, monkeypatch):
    workspace_id = "error_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)
    
    # Ingest 1 doc
    pipeline.run(ingest_request.model_copy(update={"workspace_id": workspace_id, "promotion_mode": "sync"}))
    
    # Trigger job
    from kogwistar.engine_core.models import Node, Grounding, Span
    job_req = Node(
        label="Crashing Job",
        type="entity",
        summary="Simulate LLM or Logic failure",
        mentions=[Grounding(spans=[Span(doc_id="workflow", start_char=0, end_char=1, excerpt="crash", document_page_url="dummy", collection_page_url="dummy", insertion_method="auto")])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
        }
    )
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(job_req)
        
    # Mock a crash in _step_distill by re-registering it in the resolver
    worker = MaintenanceWorker(engines)
    def mock_distill(*args, **kwargs):
        raise RuntimeError("Distillation Logic Crash")
        
    worker.resolver.register("distill")(mock_distill)
    
    # Run Worker - should not raise, but mark job as failed
    worker.process_pending_jobs(workspace_id)
    
    # Verify execution trace reflects failure
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        runs = engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(job_req.id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) == 1
        run_id = runs[0].metadata.get("run_id")
        
        steps = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_step_exec",
                "op": "distill"
            }
        )
        assert len(steps) == 1
        # The resolver wraps all handler exceptions into RunFailure, which the runtime persists as status="failure"
        assert steps[0].metadata.get("status") in ("failure", "error")


def test_wisdom_distillation_pydantic_validation(pipeline):
    """Negative test: Ensure Span validation fails with incomplete data."""
    from kogwistar.engine_core.models import Span
    
    # Missing required fields like collection_page_url
    with pytest.raises(ValidationError):
        Span(
            doc_id="test",
            start_char=0,
            end_char=10,
            excerpt="test"
        )


def test_wisdom_distillation_eager_mode_manual_trigger(pipeline, ingest_request):
    """Verify that MaintenanceWorker can be initialized in eager mode (as per requirements)."""
    engines = pipeline.engines
    worker = MaintenanceWorker(engines, eager_mode=True)
    assert worker.eager_mode is True
    
    # In eager mode, we can still process jobs normally
    workspace_id = "eager_test"
    materialize_maintenance_designs(engines.workflow)
    
    from kogwistar.engine_core.models import Node, Span, Grounding
    job_req = Node(
        label="Eager Job",
        type="entity",
        summary="Testing eager worker",
        mentions=[Grounding(spans=[Span(doc_id="workflow", start_char=0, end_char=1, excerpt="eager", document_page_url="dummy", collection_page_url="dummy", insertion_method="auto")])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
        }
    )
    ns = WorkspaceNamespaces(workspace_id)
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(job_req)
        
    worker.process_pending_jobs(workspace_id)
    
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        # Verify Trace: WorkflowRunNode should exist
        runs = engines.conversation.read.get_nodes(
            where={
                "turn_node_id": str(job_req.id),
                "entity_type": "workflow_run",
            }
        )
        assert len(runs) == 1
        run_id = runs[0].metadata.get("run_id")
        
        # Verify authoritative completion event
        completes = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_completed"
            }
        )
        assert len(completes) == 1
        
        # Verify Steps: distill step should be ok
        steps = engines.conversation.read.get_nodes(
            where={
                "run_id": run_id,
                "entity_type": "workflow_step_exec",
                "op": "distill",
                "status": "ok"
            }
        )
        assert len(steps) == 1
