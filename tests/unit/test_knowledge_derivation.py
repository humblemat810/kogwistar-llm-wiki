from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.conftest import _build_engine

from kogwistar_llm_wiki import IngestPipeline, NamespaceEngines
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar.engine_core.models import Grounding, Span
from kogwistar.runtime.models import WorkflowStepExecNode
import json


def _decode_metadata_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _sync_request(request, **updates):
    return request.model_copy(update={"promotion_mode": "sync", **updates})


def _run_sync_ingest(pipeline, request):
    artifacts = pipeline.run(request)
    assert artifacts.maintenance_job_id
    assert artifacts.promoted_entity_id is not None
    return artifacts


def _run_sync_ingest_with_trace(pipeline, request):
    workspace_id = request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)
    source_document_id = pipeline._source_document_id(request)

    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=ns.conv_fg,
    )
    parse_result = pipeline.parse_source(
        request=request,
        source_document_id=source_document_id,
    )
    graph_extraction = pipeline.translate_parse_result(
        parse_result=parse_result,
        source_document_id=source_document_id,
    )
    pipeline.ingest_parse_result(
        request=request,
        source_document_id=source_document_id,
        graph_extraction=graph_extraction,
        namespace=ns.conv_fg,
    )
    maintenance_job_id = pipeline.create_maintenance_request(
        request=request,
        source_document_id=source_document_id,
        namespace=ns.conv_bg,
    )
    candidate_link_id = pipeline.create_candidate_link(
        request=request,
        source_document_id=source_document_id,
        parse_result=parse_result,
        namespace=ns.conv_bg,
    )
    promotion_evidence_pack_id, promotion_evidence_pack_digest = (
        pipeline.create_promotion_evidence_pack(
            request=request,
            source_document_id=source_document_id,
            candidate_link_id=candidate_link_id,
            graph_extraction=graph_extraction,
            namespace=ns.conv_bg,
        )
    )
    promotion_candidate_id = pipeline.create_promotion_candidate(
        request=request,
        source_document_id=source_document_id,
        candidate_link_id=candidate_link_id,
        promotion_evidence_pack_id=promotion_evidence_pack_id,
        promotion_evidence_pack_digest=promotion_evidence_pack_digest,
        lineage_node_ids=[source_document_id, candidate_link_id],
        lineage_edge_ids=[],
        namespace=ns.conv_bg,
    )
    promotion_decision = pipeline.policies.promotion.decide(
        promotion_mode=request.promotion_mode,
        auto_accept_threshold=request.auto_accept_threshold,
        metadata={
            "workspace_id": request.workspace_id,
            "source_document_id": source_document_id,
            "promotion_candidate_id": promotion_candidate_id,
            "promotion_evidence_pack_id": promotion_evidence_pack_id,
        },
    )
    promoted_entity_id = None
    if promotion_decision.should_promote:
        promoted_entity_id = pipeline.promote_to_knowledge(
            request=request,
            source_document_id=source_document_id,
            promotion_candidate_id=promotion_candidate_id,
            promotion_evidence_pack_id=promotion_evidence_pack_id,
            promotion_evidence_pack_digest=promotion_evidence_pack_digest,
            promotion_decision=promotion_decision,
            namespace=ns.curated_kg_space,
        )

    return SimpleNamespace(
        workspace_id=workspace_id,
        namespaces=ns,
        source_document_id=source_document_id,
        parse_result=parse_result,
        graph_extraction=graph_extraction,
        maintenance_job_id=maintenance_job_id,
        candidate_link_id=candidate_link_id,
        promotion_evidence_pack_id=promotion_evidence_pack_id,
        promotion_evidence_pack_digest=promotion_evidence_pack_digest,
        promotion_candidate_id=promotion_candidate_id,
        promotion_decision=promotion_decision,
        promoted_entity_id=promoted_entity_id,
    )


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
        assert all(step.metadata.get("workspace_id") == workspace_id for step in step_execs)

    done_jobs = engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        status="DONE",
        limit=10,
    )
    assert len(done_jobs) >= 1
    assert {str(job.job_id) for job in done_jobs} >= {str(art1.maintenance_job_id), str(art2.maintenance_job_id)}


def test_knowledge_derivation_preserves_promotion_provenance_walk(pipeline, ingest_request):
    workspace_id = "provenance_walk_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    traced = _run_sync_ingest_with_trace(
        pipeline,
        _sync_request(
            ingest_request,
            workspace_id=workspace_id,
            title="Traceable Entity",
            source_uri="file://traceable_entity.txt",
        ),
    )

    worker = MaintenanceWorker(engines)
    worker.process_pending_jobs(workspace_id)

    with _temporary_namespace(engines.kg, ns.derived_knowledge):
        derived_nodes = engines.kg.read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )

    assert len(derived_nodes) == 1
    derived = derived_nodes[0]
    assert derived.metadata.get("artifact_kind") == "derived_knowledge"
    assert derived.metadata.get("source_node_ids")
    assert derived.metadata.get("replaces_ids") is not None
    assert derived.metadata["source_node_ids"] == [traced.promoted_entity_id]

    with _temporary_namespace(engines.kg, ns.curated_kg_space):
        promoted_nodes = engines.kg.read.get_nodes(ids=[traced.promoted_entity_id])

    assert len(promoted_nodes) == 1
    promoted = promoted_nodes[0]
    assert promoted.metadata.get("artifact_kind") == "promoted_knowledge"
    assert promoted.metadata.get("workspace_id") == workspace_id
    assert promoted.metadata.get("promotion_candidate_id") == traced.promotion_candidate_id
    assert promoted.metadata.get("promotion_evidence_pack_id") == traced.promotion_evidence_pack_id
    assert promoted.metadata.get("promotion_evidence_pack_digest")
    assert promoted.metadata.get("promotion_decision_reason") == (
        "explicit promotion approval accepted by default policy"
    )
    decision_metadata = _decode_metadata_json(promoted.metadata.get("promotion_decision_metadata"))
    assert decision_metadata.get("promotion_approved") is True

    with _temporary_namespace(engines.conversation, ns.conv_bg):
        evidence_packs = engines.conversation.read.get_nodes(ids=[traced.promotion_evidence_pack_id])

    assert len(evidence_packs) == 1
    evidence_pack = evidence_packs[0]
    assert evidence_pack.metadata.get("artifact_kind") == "promotion_evidence_pack"
    assert evidence_pack.metadata.get("workspace_id") == workspace_id
    assert evidence_pack.metadata.get("evidence_role") == "promotion"
    assert evidence_pack.metadata.get("created_from") == "parsed_graph_extraction"
    assert evidence_pack.metadata.get("candidate_link_id") == traced.candidate_link_id
    assert evidence_pack.metadata.get("evidence_pack_hash")

    parsed_node_ids = sorted(str(node.id) for node in traced.graph_extraction.nodes)
    parsed_edge_ids = sorted(str(edge.id) for edge in traced.graph_extraction.edges)
    digest = _decode_metadata_json(evidence_pack.metadata.get("promotion_evidence_pack_digest"))
    assert sorted(evidence_pack.metadata.get("node_ids", [])) == parsed_node_ids
    assert sorted(evidence_pack.metadata.get("edge_ids", [])) == parsed_edge_ids
    assert digest.get("node_ids") == parsed_node_ids
    assert digest.get("edge_ids") == parsed_edge_ids
    assert digest.get("evidence_pack_hash") == (
        evidence_pack.metadata.get("evidence_pack_hash")
    )

    assert digest.get("evidence_pack_hash")
    assert promoted.metadata.get("promotion_evidence_pack_id") == str(evidence_pack.id)
    assert promoted.metadata.get("promotion_candidate_id") == traced.promotion_candidate_id


def test_knowledge_derivation_replay_reuses_existing_artifact(pipeline, ingest_request):
    workspace_id = "replay_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    _run_sync_ingest(
        pipeline,
        _sync_request(
            ingest_request,
            workspace_id=workspace_id,
            title="Replay Entity",
            source_uri="file://replay.txt",
        ),
    )

    worker = MaintenanceWorker(engines)
    ctx = SimpleNamespace(state_view={"workspace_id": workspace_id, "_deps": {"engines": engines}})
    worker._step_distill(ctx)
    worker._step_distill(ctx)

    with _temporary_namespace(engines.kg, ns.derived_knowledge):
        derived_nodes = engines.kg.read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )

    assert len(derived_nodes) == 1
    assert derived_nodes[0].label == "Replay Entity"


def test_step_distill_uses_chroma_safe_where_filter(pipeline, ingest_request, monkeypatch):
    workspace_id = "distill_filter_workspace"
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)

    _run_sync_ingest(
        pipeline,
        _sync_request(
            ingest_request,
            workspace_id=workspace_id,
            title="Filter Entity",
            source_uri="file://filter-entity.txt",
        ),
    )

    worker = MaintenanceWorker(engines)
    ctx = SimpleNamespace(state_view={"workspace_id": workspace_id, "_deps": {"engines": engines}})
    captured_where: dict[str, object] = {}
    original_get_nodes = engines.kg.read.get_nodes

    def wrapped_get_nodes(*args, **kwargs):
        where = kwargs.get("where")
        if isinstance(where, dict) and any(
            isinstance(item, dict) and item.get("artifact_kind") == "promoted_knowledge"
            for item in where.get("$and", [])
        ):
            captured_where.clear()
            captured_where.update(where)
        return original_get_nodes(*args, **kwargs)

    monkeypatch.setattr(engines.kg.read, "get_nodes", wrapped_get_nodes)

    worker._step_distill(ctx)

    assert captured_where == {
        "$and": [
            {"artifact_kind": "promoted_knowledge"},
            {"workspace_id": workspace_id},
        ]
    }


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

    repeat = successful_worker._emit_execution_wisdom_from_history(workspace_id, engines)
    assert repeat == ["distill"]

    with _temporary_namespace(engines.wisdom, ns.wisdom):
        repeated_wisdom = engines.wisdom.read.get_nodes(
            where={
                "artifact_kind": "execution_wisdom",
                "workspace_id": workspace_id,
                "step_op": "distill",
            }
        )

    assert len(repeated_wisdom) == 1


def test_execution_wisdom_derivation_filters_workspace_metadata(pipeline):
    workspace_id = "wisdom_workspace"
    other_workspace_id = "foreign_wisdom_workspace"
    ns = WorkspaceNamespaces(workspace_id)
    engines = pipeline.engines
    materialize_maintenance_designs(engines.workflow)
    worker = MaintenanceWorker(engines)

    def _step_exec(*, run_id: str, workspace_id: str, step_seq: int, op: str) -> WorkflowStepExecNode:
        return WorkflowStepExecNode(
            id=f"wf_step|{run_id}|{step_seq}",
            label=f"Step {step_seq}",
            type="entity",
            doc_id=f"wf_step|{run_id}|{step_seq}",
            summary=f"workflow_step_exec {run_id} {step_seq}",
            mentions=[Grounding(spans=[Span.from_dummy_for_conversation()])],
            properties={},
            metadata={
                "entity_type": "workflow_step_exec",
                "run_id": run_id,
                "workflow_id": "execution_wisdom_wf",
                "workflow_node_id": f"wf:execution_wisdom_wf:{op}",
                "step_seq": step_seq,
                "op": op,
                "status": "failure",
                "duration_ms": 1,
                "result_json": json.dumps(
                    {
                        "conversation_node_id": None,
                        "state_update": [],
                        "next_step_names": [],
                        "status": "failure",
                    }
                ),
                "workspace_id": workspace_id,
            },
            level_from_root=0,
            domain_id=None,
            canonical_entity_id=None,
            embedding=None,
        )

    with _temporary_namespace(engines.conversation, ns.conv_bg):
        engines.conversation.write.add_node(
            _step_exec(run_id="run-local-1", workspace_id=workspace_id, step_seq=1, op="distill")
        )
        engines.conversation.write.add_node(
            _step_exec(run_id="run-local-2", workspace_id=workspace_id, step_seq=2, op="distill")
        )
        engines.conversation.write.add_node(
            _step_exec(run_id="run-foreign-1", workspace_id=other_workspace_id, step_seq=3, op="distill")
        )

        source_where = worker.policies.wisdom.source_query(workspace_id=workspace_id).where
        source_nodes = engines.conversation.read.get_nodes(where=source_where)

    assert len(source_nodes) == 2
    assert all(node.metadata.get("workspace_id") == workspace_id for node in source_nodes)

    emitted = worker._emit_execution_wisdom_from_history(workspace_id, engines)
    assert emitted == ["distill"]

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
    assert wisdom.metadata.get("failure_count") == 2
    assert wisdom.metadata.get("workspace_id") == workspace_id


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

    with _temporary_namespace(split_engines.kg, ns.curated_kg_space):
        curated_nodes = split_engines.kg.read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )
    with _temporary_namespace(split_engines.derived_knowledge_engine(), ns.derived_knowledge):
        derived_nodes = split_engines.derived_knowledge_engine().read.get_nodes(
            where={"artifact_kind": "derived_knowledge", "workspace_id": workspace_id}
        )

    assert len(curated_nodes) == 0
    assert len(derived_nodes) == 1
    assert derived_nodes[0].label == "Split Engine Entity"
