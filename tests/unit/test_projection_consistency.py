from __future__ import annotations

import os
import shutil
from pathlib import Path
import pytest
from kogwistar_llm_wiki.projection_worker import ProjectionWorker
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


@pytest.fixture()
def sync_request(ingest_request):
    """Returns a request with promotion_mode='sync' to trigger immediate knowledge promotion."""
    return ingest_request.model_copy(update={"promotion_mode": "sync"})


def test_sequential_projection_queue_integrity(pipeline, sync_request, tmp_path):
    workspace_id = sync_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    vault_root = tmp_path / "obsidian_vault"
    vault_root.mkdir()
    
    worker = ProjectionWorker(pipeline.engines)
    
    # 1. Ingest 3 documents in sync mode
    for i in range(1, 4):
        req = sync_request.model_copy(update={"title": f"Doc {i}", "source_uri": f"file://doc{i}.txt"})
        pipeline.run(req)
        
    # 2. Verify queue structure in conv_bg
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        reqs = pipeline.engines.conversation.read.get_nodes(
            where={"artifact_kind": "projection_request", "workspace_id": workspace_id}
        )
        
    assert len(reqs) == 3
    # Sort by seq to verify chain
    reqs.sort(key=lambda r: int(r.metadata.get("seq", 0)))
    
    assert reqs[0].metadata.get("seq") == 1
    assert reqs[0].metadata.get("queue_previous_id") is None
    
    assert reqs[1].metadata.get("seq") == 2
    assert reqs[1].metadata.get("queue_previous_id") == str(reqs[0].id)
    
    assert reqs[2].metadata.get("seq") == 3
    assert reqs[2].metadata.get("queue_previous_id") == str(reqs[1].id)
    
    # 3. Drain the queue
    worker.process_pending_projections(workspace_id, str(vault_root))
    
    # 4. Verify ProjectionState in meta_sqlite (Internal store)
    latest_seq = worker._get_latest_projected_seq(workspace_id)
    assert latest_seq == 3
    
    # 5. Verify Requests are marked as completed on the graph
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        completed_reqs = pipeline.engines.conversation.read.get_nodes(
            where={"artifact_kind": "projection_request", "workspace_id": workspace_id, "status": "completed"}
        )
    assert len(completed_reqs) == 3


def test_projection_handles_failures_and_resumes(pipeline, sync_request, tmp_path, monkeypatch):
    workspace_id = sync_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    vault_root = tmp_path / "obsidian_vault_fail"
    vault_root.mkdir()
    
    worker = ProjectionWorker(pipeline.engines)
    
    # 1. Ingest 2 docs
    pipeline.run(sync_request.model_copy(update={"title": "Good Doc", "source_uri": "file://good.txt"}))
    pipeline.run(sync_request.model_copy(update={"title": "Bad Doc", "source_uri": "file://bad.txt"}))
    
    # 2. Mock a failure on the second sync
    original_sync = worker.manager.sync_obsidian_vault
    call_count = 0
    
    def mock_sync(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Obsidian Sink Crash")
        return original_sync(*args, **kwargs)
        
    monkeypatch.setattr(worker.manager, "sync_obsidian_vault", mock_sync)
    
    # 3. Process - should stop at seq=1 after seq=2 fails
    with pytest.raises(RuntimeError, match="Obsidian Sink Crash"):
        worker.process_pending_projections(workspace_id, str(vault_root))
        
    # 4. Verify state didn't advance to 2 in meta_sqlite
    latest_seq = worker._get_latest_projected_seq(workspace_id)
    assert latest_seq == 1
    
    # 5. Fix the "sink" and resume
    monkeypatch.setattr(worker.manager, "sync_obsidian_vault", original_sync)
    worker.process_pending_projections(workspace_id, str(vault_root))
    
    # Verify final state
    latest_seq = worker._get_latest_projected_seq(workspace_id)
    assert latest_seq == 2


def test_projection_gap_detection(pipeline, sync_request, tmp_path):
    workspace_id = sync_request.workspace_id
    vault_root = tmp_path / "obsidian_vault_gap"
    vault_root.mkdir()
    
    worker = ProjectionWorker(pipeline.engines)
    
    # 1. Ingest 3 docs
    for i in range(1, 4):
        pipeline.run(sync_request.model_copy(update={"title": f"Doc {i}", "source_uri": f"file://doc{i}.txt"}))
        
    # 2. Delete seq=2 from the graph (simulate data corruption or manual deletion)
    ns = WorkspaceNamespaces(workspace_id)
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        nodes = pipeline.engines.conversation.read.get_nodes(
            where={"artifact_kind": "projection_request"}
        )
        target = [n for n in nodes if int(n.metadata.get("seq", 0)) == 2]
        for n in target:
            pipeline.engines.conversation.tombstone_node(str(n.id))
            
    # 3. Drain the queue - should stop at seq=1 because seq=2 is missing
    # This verifies the strict-order guarantee: we never skip sequences.
    worker.process_pending_projections(workspace_id, str(vault_root))
    
    latest_seq = worker._get_latest_projected_seq(workspace_id)
    assert latest_seq == 1


def test_projection_rapid_ingestion_stress(pipeline, sync_request, tmp_path):
    workspace_id = sync_request.workspace_id
    worker = ProjectionWorker(pipeline.engines)
    
    # Rapid ingestion to verify sequential monotonic incrementing under churn
    for i in range(10):
        pipeline.run(sync_request.model_copy(update={"title": f"Stress {i}", "source_uri": f"file://stress{i}.txt"}))
        
    ns = WorkspaceNamespaces(workspace_id)
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        reqs = pipeline.engines.conversation.read.get_nodes(
            where={"artifact_kind": "projection_request", "workspace_id": workspace_id}
        )
        
    assert len(reqs) == 10
    seqs = sorted([int(r.metadata["seq"]) for r in reqs])
    assert seqs == list(range(1, 11))


def test_projection_empty_workspace_is_noop(pipeline, sync_request, tmp_path):
    workspace_id = "empty_ws"
    vault_root = tmp_path / "obsidian_vault_empty"
    vault_root.mkdir()
    worker = ProjectionWorker(pipeline.engines)
    
    # Drainage on a workspace with zero requests should be a safe no-op
    worker.process_pending_projections(workspace_id, str(vault_root))
    
    latest_seq = worker._get_latest_projected_seq(workspace_id)
    assert latest_seq == 0
