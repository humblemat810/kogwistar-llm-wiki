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
    
    # 2. Ingest 2 documents with the same entity name
    # We use promotion_mode='sync' to ensure they reach the KG
    req1 = ingest_request.model_copy(update={
        "title": "Doc A", "source_uri": "file://doc_a.txt", "promotion_mode": "sync"
    })
    req2 = ingest_request.model_copy(update={
        "title": "Doc B", "source_uri": "file://doc_b.txt", "promotion_mode": "sync"
    })
    
    pipeline.run(req1)
    pipeline.run(req2)
    
    # 3. Create a Maintenance Job Request manually
    # (In a real system, the IngestPipeline or a scheduler would do this)
    from kogwistar.engine_core.models import Node, Grounding, Span
    job_req = Node(
        label="Distill Wisdom (Test)",
        type="entity",
        summary="Triggering wisdom distillation",
        mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
            "status": "pending",
        }
    )
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(job_req)
        
    # 4. Run the Worker
    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs(workspace_id)
    
    # 5. Verify Wisdom Generation
    with _temporary_namespace(engines.wisdom, ns.wisdom):
        wisdom_nodes = engines.wisdom.read.get_nodes(
            where={"artifact_kind": "wisdom", "workspace_id": workspace_id}
        )
        
    assert len(wisdom_nodes) > 0
    # Both "Doc A" and "Doc B" had the label "Acme Contract" (from ingest_request fixture)
    # However we changed titles to "Doc A" and "Doc B". 
    # In the actual implementation, promote_to_knowledge uses the document title as the label.
    # So we expect two nodes unless we ensure they share a label.
    
    # Check for Doc A
    doc_a_wisdom = [n for n in wisdom_nodes if "Doc A" in n.label]
    assert len(doc_a_wisdom) == 1
    
    # Verify Grounding
    assert len(doc_a_wisdom[0].mentions) == 1
    
    # Check for Doc B
    doc_b_wisdom = [n for n in wisdom_nodes if "Doc B" in n.label]
    assert len(doc_b_wisdom) == 1
    
    # Verify Grounding (Merged Mentions)
    # Each source node had 1 mention. Total should be 1 each as they don't merge.
    assert len(doc_a_wisdom[0].mentions) == 1
    assert len(doc_b_wisdom[0].mentions) == 1
    
    # Verify Source Lineage
    source_ids_a = doc_a_wisdom[0].metadata.get("source_node_ids", [])
    assert len(source_ids_a) == 1
    source_ids_b = doc_b_wisdom[0].metadata.get("source_node_ids", [])
    assert len(source_ids_b) == 1


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
        mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
            "status": "pending",
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
            where={"workspace_id": workspace_id}
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
        mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
            "status": "pending",
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
    
    # Verify job status in graph
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        nodes = engines.conversation.read.get_nodes(where={"workspace_id": workspace_id, "artifact_kind": "maintenance_job_request"})
        req = nodes[0]
        assert req.metadata.get("status") == "failed"


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
    
    from kogwistar.engine_core.models import Node, Span
    job_req = Node(
        label="Eager Job",
        type="entity",
        summary="Testing eager worker",
        mentions=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "maintenance_job_request",
            "trigger_type": "manual",
            "status": "pending",
        }
    )
    ns = WorkspaceNamespaces(workspace_id)
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(job_req)
        
    worker.process_pending_jobs(workspace_id)
    
    with _temporary_namespace(engines.conversation, ns.conv_bg):
        nodes = engines.conversation.read.get_nodes(where={"workspace_id": workspace_id})
        assert nodes[0].metadata.get("status") == "completed"
