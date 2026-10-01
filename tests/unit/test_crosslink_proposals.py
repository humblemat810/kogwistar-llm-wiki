from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from kogwistar.engine_core import AtomicMutationCapability
from kogwistar.engine_core.models import Document, Grounding, Node, Span
from pydantic import ValidationError

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.maintenance.crosslink_proposals import (
    CrosslinkEvidence,
    CrosslinkProposalResponse,
    CrosslinkReviewDecision,
)
from kogwistar_llm_wiki.maintenance.crosslink_reviews import (
    CrosslinkGroupReviewService,
    CrosslinkReviewConflict,
)
from kogwistar_llm_wiki.maintenance.maintenance_strategies import MaintenanceJobExecutionContext
from kogwistar_llm_wiki.maintenance.maintenance_designs import (
    build_crosslink_group_design,
)
from kogwistar_llm_wiki.maintenance.maintenance_patch_apply import (
    apply_maintenance_patch,
)
from kogwistar_llm_wiki.maintenance.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from kogwistar_llm_wiki.maintenance.maintenance_policy import (
    CROSSLINK_GROUP_WORKFLOW_ID,
    workflow_id_for_maintenance_kind,
)
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.maintenance.maintenance_guards import source_digest
from kogwistar_llm_wiki.maintenance.worker_execution import MaintenanceExecutionWorkerMixin
from kogwistar_llm_wiki.maintenance.worker_selection import MaintenanceSelectionWorkerMixin
from kogwistar_llm_wiki.maintenance.state import (
    durable_maintenance_usage,
    metadata_mapping,
)


def test_proposal_response_is_bounded_and_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CrosslinkProposalResponse.model_validate({"groups": [], "node_ids": ["invented"]})
    with pytest.raises(ValidationError):
        CrosslinkProposalResponse.model_validate(
            {"groups": [{"group_id": "g", "rationale": "x", "operations": []}]}
        )


def test_proposal_contract_rejects_duplicate_group_and_supersession_ids() -> None:
    operation = {
        "left_evidence_id": "left",
        "right_evidence_id": "right",
        "relation": "references",
        "rationale": "both sources support this",
        "supersedes_edge_id": "edge-1",
    }
    group = {
        "group_id": "same",
        "rationale": "a group",
        "operations": [operation, {**operation, "relation": "mentions"}],
    }
    with pytest.raises(ValidationError, match="supersede each edge at most once"):
        CrosslinkProposalResponse.model_validate({"groups": [group]})
    with pytest.raises(ValidationError, match="group_id values must be unique"):
        CrosslinkProposalResponse.model_validate({
            "groups": [
                {"group_id": "same", "rationale": "one", "operations": [operation]},
                {"group_id": "same", "rationale": "two", "operations": [
                    {**operation, "supersedes_edge_id": None}
                ]},
            ]
        })


def test_evidence_requires_sha256_revision_digest() -> None:
    with pytest.raises(ValidationError):
        CrosslinkEvidence(
            evidence_id="e1",
            node_id="n1",
            source_document_id="source",
            source_revision_id="revision",
            revision_document_id="revision-doc",
            source_digest="not-a-digest",
            start_char=0,
            end_char=1,
            excerpt="x",
        )


def test_group_atomicity_is_carried_into_patch_contract() -> None:
    provenance = MaintenanceProvenance(
        source_document_id="source-a",
        source_pointers=[
            {"doc_id": "source-a", "start_char": 0, "end_char": 1},
            {"doc_id": "source-b", "start_char": 0, "end_char": 1},
        ],
        maintenance_run_id="run",
        confidence=0.9,
    )
    patch = MaintenancePatch(
        patch_id="atomic-group",
        intent=MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
        scope=MaintenanceScope(workspace_id="demo"),
        requires_atomic_group=True,
        operations=[
            MaintenancePatchOperation(
                operation_id="edge",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:1",
                from_node_id="ws:demo:node:a",
                to_node_id="ws:demo:node:b",
                relation="related_to",
                provenance=provenance,
            )
        ],
    )
    assert patch.requires_atomic_replacement is True


def test_required_atomic_group_is_rejected_on_nontransactional_backend() -> None:
    class Read:
        def get_nodes(self, ids=None, **_kwargs):
            values = [
                Node(
                    id=item,
                    label=item,
                    type="entity",
                    summary=item,
                    doc_id="d1",
                    mentions=[Grounding(spans=[Span.from_dummy_for_workflow(item)])],
                    metadata={"workspace_id": "demo"},
                )
                for item in (ids or ["ws:demo:node:a", "ws:demo:node:b"])
            ]
            return values

        def get_edges(self, **_kwargs):
            return []

    class Write:
        def __init__(self):
            self.calls = 0

        def add_edge(self, _edge):
            self.calls += 1

    class Engine:
        atomic_mutation_capability = AtomicMutationCapability(
            mode="eventual", reason="no transaction support"
        )

        def __init__(self):
            self.read = Read()
            self.write = Write()

    provenance = MaintenanceProvenance(
        source_document_id="d1",
        source_pointers=[
            {"doc_id": "d1", "start_char": 0, "end_char": 1},
            {"doc_id": "d2", "start_char": 0, "end_char": 1},
        ],
        maintenance_run_id="run",
        confidence=0.9,
    )
    patch = MaintenancePatch(
        patch_id="must-not-write",
        intent=MaintenanceIntent.ADD_CROSSLINK,
        scope=MaintenanceScope(workspace_id="demo"),
        requires_atomic_group=True,
        operations=[
            MaintenancePatchOperation(
                operation_id="edge",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:1",
                from_node_id="ws:demo:node:a",
                to_node_id="ws:demo:node:b",
                relation="relates",
                properties={"crosslink_status": "accepted"},
                provenance=provenance,
            )
        ],
    )
    engine = Engine()
    result = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")
    assert result.status.value == "rejected"
    assert any(issue.code == "atomic_mutation_required" for issue in result.validation.issues)
    assert engine.write.calls == 0


def test_crosslink_workflow_design_names_all_lifecycle_stages() -> None:
    design = build_crosslink_group_design()
    labels = {node.label for node in design.nodes}
    assert design.workflow_id == CROSSLINK_GROUP_WORKFLOW_ID
    assert {
        "Select Bounded Candidates",
        "Assemble Source Evidence",
        "Propose Cross-Link Groups",
        "Validate Groups",
        "Critic Review Per Group",
        "Route By Approval Policy",
        "Persist Pending Review",
        "Apply Approved Group",
        "Record Group Outcome",
        "Continue Exploration",
    } <= labels
    assert workflow_id_for_maintenance_kind("document_propose_crosslinks") == design.workflow_id
    assert len(design.edges) >= len(design.nodes)


def test_review_decisions_are_durable_idempotent_and_group_scoped(pipeline) -> None:
    workspace_id = "crosslink-review-tests"
    ns = WorkspaceNamespaces(workspace_id)
    artifact_id = "crosslink-review-artifact-1"
    artifact = Node(
        id=artifact_id,
        label="Review group",
        type="entity",
        summary="review this group",
        doc_id="source",
        mentions=[Grounding(spans=[Span.from_dummy_for_workflow(artifact_id)])],
        metadata={
            "artifact_kind": "crosslink_group_review",
            "workspace_id": workspace_id,
            "review_status": "pending",
            "decision_version": 1,
            "group_id": "group-1",
        },
    )
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        pipeline.engines.conversation.write.add_node(artifact)

    service = CrosslinkGroupReviewService(pipeline.engines)
    assert [str(item.id) for item in service.list_pending(workspace_id=workspace_id)] == [artifact_id]
    decision = CrosslinkReviewDecision(
        artifact_id=artifact_id,
        decision="reject",
        expected_version=1,
    )
    first = service.decide_batch(workspace_id=workspace_id, decisions=[decision], actor_id="user:reviewer")
    second = service.decide_batch(workspace_id=workspace_id, decisions=[decision], actor_id="user:reviewer")
    assert first[0]["status"] == "decided"
    assert second[0]["status"] == "already_decided"
    assert service.list_pending(workspace_id=workspace_id) == []

    opposite = decision.model_copy(update={"decision": "approve"})
    with pytest.raises(CrosslinkReviewConflict):
        service._decide_one(workspace_id, "user:reviewer", opposite)


@pytest.mark.parametrize("max_calls, expected_critic_calls", [(2, 1), (1, 0)])
def test_fake_provider_human_review_pins_every_source_and_queues_fenced_revalidation(
    pipeline, max_calls: int, expected_critic_calls: int
) -> None:
    workspace_id = "crosslink-provider-e2e"
    ns = WorkspaceNamespaces(workspace_id)
    source_rows = [
        ("logical-a", "revision-a", "revision-doc-a", "A system uses retrieval evidence."),
        ("logical-b", "revision-b", "revision-doc-b", "Retrieval evidence supports the system."),
    ]
    nodes: list[Node] = []
    for index, (logical_id, revision_id, revision_doc_id, content) in enumerate(source_rows):
        digest = source_digest(content)
        document = Document(
            id=revision_doc_id,
            content=content,
            type="text",
            metadata={
                "workspace_id": workspace_id,
                "logical_source_document_id": logical_id,
                "source_revision_id": revision_id,
                "source_digest": digest,
                "revision_document_id": revision_doc_id,
            },
        )


        with _temporary_namespace(pipeline.engines.kg, ns.source_space):
            pipeline.engines.kg.write.add_document(document)
        node_id = f"ws:{workspace_id}:node:{index}"
        nodes.append(Node(
            id=node_id,
            label=f"Concept {index}",
            type="entity",
            summary="A retrieval evidence concept",
            doc_id=revision_doc_id,
            mentions=[Grounding(spans=[Span(
                doc_id=revision_doc_id,
                start_char=0,
                end_char=len(content),
                excerpt=content,
                document_page_url="",
                collection_page_url="",
                insertion_method="test",
            )])],
            metadata={
                "workspace_id": workspace_id,
                "source_document_id": logical_id,
            },
        ))
    with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
        for node in nodes:
            pipeline.engines.kg.write.add_node(node)

    traces: list[dict[str, object]] = []
    replies: list[dict[str, object]] = []
    critic_calls: list[object] = []

    class Worker(MaintenanceExecutionWorkerMixin, MaintenanceSelectionWorkerMixin):
        pass

    worker = object.__new__(Worker)
    worker.engines = pipeline.engines
    worker.crosslink_proposer = lambda evidence, _ctx: {
        "groups": [{
            "group_id": "provider-group",
            "rationale": "The sources describe the same retrieval evidence system.",
            "indivisible": True,
            "operations": [{
                "left_evidence_id": evidence[0]["evidence_id"],
                "right_evidence_id": evidence[1]["evidence_id"],
                "relation": "supports",
                "rationale": "Both excerpts describe retrieval evidence.",
            }],
        }]
    }
    def review(request, _ctx):
        critic_calls.append(request)
        return {
            "verdict": "approve",
            "explanation": "Both independent source excerpts support this link.",
            "evidence_ids": [item["evidence_id"] for item in request["evidence"]],
        }

    worker.crosslink_critic = review
    worker.provider_settings = None
    worker._claim_lost = threading.Event()
    worker._emit_trace = lambda event, **payload: traces.append({"event": event, **payload})
    worker._emit_lane_reply = lambda **payload: replies.append(payload)
    worker._advance_maintenance_plan = lambda _ctx: True
    worker._acknowledge_job = lambda _ctx: pytest.fail("the plan should advance")
    ctx = MaintenanceJobExecutionContext(
        workspace_id=workspace_id,
        job=None,
        job_id="provider-e2e-job",
        payload={
            "workspace_id": workspace_id,
            "maintenance_candidates": [
                {"candidate_id": str(node.id)} for node in nodes
            ],
            "crosslink_approval_mode": "human",
            "budgets": {"max_llm_calls": max_calls},
        },
        request_node=None,
        request_node_id="provider-e2e-request",
        lane_message_id="",
        maintenance_kind="document_propose_crosslinks",
    )

    with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
        loaded_nodes = pipeline.engines.kg.read.get_nodes(ids=[str(node.id) for node in nodes], limit=2)
    assert len(loaded_nodes) == 2, loaded_nodes
    assert loaded_nodes[0].mentions, loaded_nodes[0]
    with _temporary_namespace(pipeline.engines.kg, ns.source_space):
        loaded_document = pipeline.engines.kg.read.get_document("revision-doc-a")
    assert metadata_mapping(loaded_document).get("logical_source_document_id") == "logical-a"
    evidence = worker._collect_crosslink_evidence(ctx)
    assert len(evidence) == 2, evidence
    worker._propose_background_crosslink_groups(ctx)

    service = CrosslinkGroupReviewService(pipeline.engines)
    pending = service.list_pending(workspace_id=workspace_id)
    assert len(pending) == 1, (replies, traces)
    assert len(critic_calls) == expected_critic_calls
    assert ctx.payload["maintenance_budget_state"]["call_used"] == max_calls
    assert durable_maintenance_usage(
        pipeline.engines.conversation.meta_sqlite,
        namespace=ns.usage_events,
        maintenance_job_id="provider-e2e-job",
    )["call_used"] == max_calls
    metadata = dict(pending[0].metadata or {})
    fences = json.loads(str(metadata["source_revision_fences"]))
    assert {item["source_document_id"] for item in fences} == {"logical-a", "logical-b"}
    assert replies[-1]["payload"]["pending_groups"] == 1
    assert any(item["event"] == "maintenance_crosslink_group_reviewed" for item in traces)
    workflow_node_ids = {
        str(node.id) for node in build_crosslink_group_design().nodes
    }
    stage_traces = [
        item for item in traces
        if item.get("event") == "maintenance_crosslink_workflow_stage"
    ]
    assert {str(item["workflow_stage"]) for item in stage_traces} >= {
        "select", "evidence", "propose", "validate", "critic", "route",
        "pending", "outcome", "continue",
    }
    assert all(str(item["workflow_node_id"]) in workflow_node_ids for item in stage_traces)

    decision = service.decide_batch(
        workspace_id=workspace_id,
        decisions=[{
            "artifact_id": str(pending[0].id),
            "decision": "approve",
            "expected_version": 1,
        }],
        actor_id="user:reviewer",
        authority_claims={
            "sub": "user:reviewer",
            "scope": "write",
            "workspaces": [workspace_id],
        },
    )
    assert decision[0]["status"] == "decided"
    queued = pipeline.engines.conversation.meta_sqlite.list_index_jobs(
        namespace=ns.maintenance_jobs,
        limit=20,
    )
    approval_job = next(
        item for item in queued
        if item.index_kind == "maintenance_job:document_validate_crosslinks"
    )
    payload = json.loads(approval_job.payload_json)
    assert payload["authority_required"] is True
    assert len(payload["source_revision_fences"]) == 2
    patch = MaintenancePatch.model_validate(payload["patch"])
    MaintenanceExecutionWorkerMixin._validate_crosslink_authority(
        worker,
        MaintenanceJobExecutionContext(
            workspace_id=workspace_id,
            job=None,
            job_id=approval_job.job_id,
            payload=payload,
            request_node=None,
            request_node_id=payload["request_node_id"],
            lane_message_id="",
            maintenance_kind="document_validate_crosslinks",
        ),
        patch,
    )
    first_operation = patch.operations[0]
    assert first_operation.provenance is not None
    pointers = first_operation.provenance.source_pointers
    altered_pointer = {**pointers[0], "excerpt": "fabricated excerpt"}
    altered_operation = first_operation.model_copy(update={
        "provenance": first_operation.provenance.model_copy(update={
            "source_pointers": [altered_pointer, pointers[1]],
        })
    })
    altered_patch = patch.model_copy(update={"operations": [altered_operation]})
    with pytest.raises(ValueError, match="excerpt|text"):
        MaintenanceExecutionWorkerMixin._validate_crosslink_authority(
            worker,
            MaintenanceJobExecutionContext(
                workspace_id=workspace_id,
                job=None,
                job_id=approval_job.job_id,
                payload=payload,
                request_node=None,
                request_node_id=payload["request_node_id"],
                lane_message_id="",
                maintenance_kind="document_validate_crosslinks",
            ),
            altered_patch,
        )


def test_real_provider_path_persists_call_and_token_usage_before_retry(
    pipeline, monkeypatch
) -> None:
    class Structured:
        def invoke(self, _messages, *, config):
            callback = config["callbacks"][0]
            callback.on_llm_start([], run_id="provider-run")
            callback.on_llm_end(
                SimpleNamespace(
                    llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2}},
                    response_metadata={},
                    generations=[],
                ),
                run_id="provider-run",
            )
            return {"parsed": {"groups": []}}

    class ChatModel:
        def with_structured_output(self, _schema):
            return Structured()

    monkeypatch.setattr(
        "kg_doc_parser.workflow_ingest.page_index.build_chat_model_for_role",
        lambda *_args: ChatModel(),
    )

    class Worker(MaintenanceExecutionWorkerMixin):
        pass

    worker = object.__new__(Worker)
    worker.engines = pipeline.engines
    worker.provider_settings = SimpleNamespace(
        parser=SimpleNamespace(provider="fake", model="fake-model")
    )
    worker._emit_trace = lambda *_args, **_kwargs: None
    ctx = MaintenanceJobExecutionContext(
        workspace_id="crosslink-usage",
        job=None,
        job_id="crosslink-usage-job",
        payload={
            "workspace_id": "crosslink-usage",
            "source_document_id": "source-usage",
            "budgets": {"max_llm_calls": 2, "max_tokens": 100},
        },
        request_node=None,
        request_node_id="crosslink-usage-request",
        lane_message_id="",
        maintenance_kind="document_propose_crosslinks",
    )
    evidence = CrosslinkEvidence(
        evidence_id="evidence-usage",
        node_id="node-usage",
        source_document_id="logical-usage",
        source_revision_id="revision-usage",
        revision_document_id="revision-document-usage",
        source_digest="a" * 64,
        start_char=0,
        end_char=5,
        excerpt="usage",
    )

    result = worker._invoke_crosslink_proposer([evidence], ctx)

    assert result == {"groups": []}
    usage = durable_maintenance_usage(
        pipeline.engines.conversation.meta_sqlite,
        namespace=WorkspaceNamespaces(ctx.workspace_id).usage_events,
        maintenance_job_id=ctx.job_id,
    )
    assert usage["call_used"] == 1
    assert usage["token_used"] == 5
