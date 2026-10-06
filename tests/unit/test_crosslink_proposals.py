from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from kogwistar.engine_core import AtomicMutationCapability
from kogwistar.engine_core.models import Document, Grounding, Node, Span
from kogwistar.server.auth_middleware import reset_claims_ctx, set_claims_ctx
from pydantic import ValidationError

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.maintenance.crosslink_context import (
    CrosslinkContextBudget,
    pack_crosslink_context,
)
from kogwistar_llm_wiki.maintenance.crosslink_proposals import (
    CrosslinkEvidence,
    CrosslinkProposalResponse,
    CrosslinkReviewDecision,
)
from kogwistar_llm_wiki.maintenance.crosslink_reviews import (
    CrosslinkGroupReviewService,
    CrosslinkReviewConflict,
)
from kogwistar_llm_wiki.maintenance.maintenance_designs import (
    build_crosslink_group_design,
)
from kogwistar_llm_wiki.maintenance.maintenance_guards import source_digest
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
from kogwistar_llm_wiki.maintenance.maintenance_strategies import (
    MaintenanceJobExecutionContext,
)
from kogwistar_llm_wiki.maintenance.state import (
    durable_maintenance_usage,
    metadata_mapping,
)
from kogwistar_llm_wiki.maintenance.worker_execution import (
    MaintenanceExecutionWorkerMixin,
)
from kogwistar_llm_wiki.maintenance.worker_selection import (
    MaintenanceSelectionWorkerMixin,
)
from kogwistar_llm_wiki.utils import _temporary_namespace


def test_crosslink_context_uses_core_packing_and_all_optional_limits() -> None:
    nodes = [
        {
            "context_kind": "neighbor_node",
            "node_id": f"ws:demo:node:{index}",
            "summary": "neighbor summary " + ("x" * 40),
        }
        for index in range(3)
    ]
    edges = [
        {
            "context_kind": "neighbor_edge",
            "edge_id": f"ws:demo:edge:{index}",
            "source_ids": ["ws:demo:node:0"],
            "target_ids": [f"ws:demo:node:{index + 1}"],
            "relation": "related_to",
        }
        for index in range(2)
    ]
    budget = CrosslinkContextBudget(
        max_nodes=2,
        max_edges=1,
        max_tokens=200,
        max_characters=500,
    )

    first = pack_crosslink_context(nodes, edges, budget=budget)
    second = pack_crosslink_context(list(reversed(nodes)), list(reversed(edges)), budget=budget)

    assert first == second
    assert len(first["nodes"]) <= 2
    assert len(first["edges"]) <= 1
    assert first["estimated_tokens"] <= 200
    assert first["characters"] <= 500
    assert first["omitted_nodes"] >= 1
    assert first["omitted_edges"] >= 1


def test_crosslink_context_budget_defaults_and_compatibility_aliases() -> None:
    assert CrosslinkContextBudget.from_payload({}) == CrosslinkContextBudget()
    assert CrosslinkContextBudget.from_payload({
        "crosslink_context": {"max_nodes": 3, "max_chars": 700}
    }) == CrosslinkContextBudget(max_nodes=3, max_edges=24, max_characters=700)
    assert CrosslinkContextBudget.from_payload({
        "crosslink_context_max_edges": 5,
        "crosslink_context_max_tokens": 900,
    }) == CrosslinkContextBudget(max_nodes=16, max_edges=5, max_tokens=900)
    with pytest.raises(ValueError, match="max_nodes must be non-negative"):
        CrosslinkContextBudget.from_payload({"max_nodes": -1})


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
    source_values = [
        ("logical-a", "revision-a", "revision-doc-a", "First exact excerpt."),
        ("logical-b", "revision-b", "revision-doc-b", "Second exact excerpt."),
    ]
    source_pointers: list[dict[str, object]] = []
    endpoint_ids = ["ws:crosslink-review-tests:node:a", "ws:crosslink-review-tests:node:b"]
    for index, (logical_id, revision_id, document_id, content) in enumerate(source_values):
        digest = source_digest(content)
        with _temporary_namespace(pipeline.engines.kg, ns.source_space):
            pipeline.engines.kg.write.add_document(Document(
                id=document_id,
                content=content,
                type="text",
                metadata={
                    "workspace_id": workspace_id,
                    "logical_source_document_id": logical_id,
                    "source_revision_id": revision_id,
                    "revision_document_id": document_id,
                    "source_digest": digest,
                    "acl_scope": "team-a",
                },
            ))
        with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
            pipeline.engines.kg.write.add_node(Node(
                id=endpoint_ids[index],
                label=f"Concept {index}",
                type="entity",
                summary="Grounded review endpoint",
                doc_id=document_id,
                mentions=[Grounding(spans=[Span(
                    doc_id=document_id,
                    start_char=0,
                    end_char=len(content),
                    excerpt=content,
                    document_page_url="",
                    collection_page_url="",
                    insertion_method="test",
                )])],
                metadata={"workspace_id": workspace_id, "acl_scope": "team-a"},
            ))
        source_pointers.append({
            "doc_id": document_id,
            "source_cluster_id": document_id,
            "source_document_id": logical_id,
            "source_revision_id": revision_id,
            "source_digest": digest,
            "workspace_id": workspace_id,
            "start_char": 0,
            "end_char": len(content),
            "excerpt": content,
        })
    patch = MaintenancePatch(
        patch_id="review-patch",
        intent=MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
        scope=MaintenanceScope(workspace_id=workspace_id),
        rationale="review fixture",
        operations=[MaintenancePatchOperation(
            operation_id="review-edge",
            kind=MaintenanceOperationKind.ADD_EDGE,
            edge_id="ws:crosslink-review-tests:edge:review",
            from_node_id=endpoint_ids[0],
            to_node_id=endpoint_ids[1],
            relation="supports",
            provenance=MaintenanceProvenance(
                source_document_id="revision-doc-a",
                source_pointers=source_pointers,
                maintenance_run_id="review-test-run",
                confidence=1.0,
            ),
        )],
    )
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
            "patch_json": json.dumps(patch.model_dump(mode="json")),
        },
    )
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        pipeline.engines.conversation.write.add_node(artifact)

    service = CrosslinkGroupReviewService(pipeline.engines)
    claims_token = set_claims_ctx({"security_scope": "team-a"})
    try:
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
    finally:
        reset_claims_ctx(claims_token)

    claims_token = set_claims_ctx({"security_scope": "team-b"})
    try:
        assert service.list_pending(workspace_id=workspace_id) == []
        pending_artifact = artifact.model_copy(update={
            "id": "crosslink-review-artifact-other-scope",
            "metadata": {
                **dict(artifact.metadata or {}),
                "review_status": "pending",
                "decision_version": 1,
            },
        })
        with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
            pipeline.engines.conversation.write.add_node(pending_artifact)
        with pytest.raises(PermissionError, match="security scope"):
            service._decide_one(
                workspace_id,
                "user:other-scope",
                CrosslinkReviewDecision(
                    artifact_id=str(pending_artifact.id),
                    decision="approve",
                    expected_version=1,
                ),
            )
    finally:
        reset_claims_ctx(claims_token)


def _fake_proposal_worker(
    evidence: list[CrosslinkEvidence],
    proposal: object,
    critic: object,
    *,
    approval_mode: str = "automatic",
    parse_quality: str | None = None,
) -> tuple[object, dict[str, list[object]]]:
    class Worker(MaintenanceExecutionWorkerMixin):
        pass

    worker = object.__new__(Worker)
    records: dict[str, list[object]] = {
        "critic_calls": [],
        "reviews": [],
        "rejections": [],
        "apply_jobs": [],
        "finished": [],
        "traces": [],
    }
    worker._collect_crosslink_evidence = lambda _ctx: evidence
    worker._invoke_crosslink_proposer = lambda _evidence, _ctx: proposal

    def run_critic(group, group_evidence, _ctx):
        records["critic_calls"].append((group, group_evidence))
        return critic(group, group_evidence) if callable(critic) else critic

    worker._invoke_crosslink_critic = run_critic
    worker._validate_crosslink_authority = lambda *_args: None
    worker._persist_crosslink_group_review = (
        lambda _ctx, group_id, patch, group, review, *, status: (
            records["reviews"].append({
                "group_id": group_id,
                "patch": patch,
                "group": group,
                "critic": review,
                "status": status,
            })
            or f"artifact:{group_id}"
        )
    )
    worker._persist_crosslink_group_rejection = (
        lambda _ctx, group_id, group, error: (
            records["rejections"].append({
                "group_id": group_id,
                "group": group,
                "error": error,
            })
            or f"artifact:{group_id}"
        )
    )
    worker._enqueue_crosslink_group_apply = (
        lambda _ctx, group_id, patch: records["apply_jobs"].append((group_id, patch))
    )
    worker._finish_crosslink_proposal = (
        lambda _ctx, **result: records["finished"].append(result)
    )
    worker._emit_trace = lambda event, **fields: records["traces"].append(
        {"event": event, **fields}
    )
    worker._claim_lost = threading.Event()
    worker.provider_settings = None
    payload: dict[str, object] = {"crosslink_approval_mode": approval_mode}
    if parse_quality is not None:
        payload["parse_quality_status"] = parse_quality
    ctx = MaintenanceJobExecutionContext(
        workspace_id="proposal-unit",
        job=None,
        job_id="proposal-unit-job",
        payload=payload,
        request_node=None,
        request_node_id="proposal-unit-request",
        lane_message_id="",
        maintenance_kind="document_propose_crosslinks",
    )
    records["context"] = [ctx]
    return worker, records


def _evidence_pair() -> list[CrosslinkEvidence]:
    return [
        CrosslinkEvidence(
            evidence_id=f"evidence-{index}",
            node_id=f"node-{index}",
            source_document_id=f"logical-{index}",
            source_revision_id=f"revision-{index}",
            revision_document_id=f"revision-document-{index}",
            source_digest=str(index) * 64,
            start_char=0,
            end_char=5,
            excerpt="proof",
        )
        for index in range(4)
    ]


def test_crosslink_resource_leases_serialize_conflicting_runs() -> None:
    class Store:
        def __init__(self) -> None:
            self.rows: dict[tuple[str, str], dict[str, object]] = {}

        def get_named_projection(self, namespace: str, key: str) -> dict[str, object] | None:
            return self.rows.get((namespace, key))

        def compare_and_swap_named_projections(self, updates: list[dict[str, object]]) -> bool:
            for item in updates:
                current = self.rows.get((str(item["namespace"]), str(item["key"])))
                expected = (
                    item.get("expected_last_authoritative_seq"),
                    item.get("expected_last_materialized_seq"),
                )
                actual = (
                    current.get("last_authoritative_seq"),
                    current.get("last_materialized_seq"),
                ) if current else (None, None)
                if expected != actual:
                    return False
            for item in updates:
                self.rows[(str(item["namespace"]), str(item["key"]))] = dict(item)
            return True

    store = Store()
    engines = SimpleNamespace(conversation=SimpleNamespace(meta_sqlite=store))
    worker = object.__new__(MaintenanceExecutionWorkerMixin)
    worker.engines = engines
    worker.worker_id = "worker-a"
    patch = MaintenancePatch(
        patch_id="lease-patch",
        intent=MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
        scope=MaintenanceScope(workspace_id="lease-workspace"),
        operations=[
            MaintenancePatchOperation(
                operation_id="link",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="edge:shared",
                from_node_id="node:left",
                to_node_id="node:right",
                relation="related_to",
                properties={"crosslink_status": "candidate"},
            )
        ],
    )
    first = MaintenanceJobExecutionContext(
        workspace_id="lease-workspace", job=None, job_id="job-a",
        payload={"maintenance_run_id": "run-a"}, request_node=None,
        request_node_id="request-a", lane_message_id="",
        maintenance_kind="document_validate_crosslinks",
    )
    second = first.__class__(
        workspace_id="lease-workspace", job=None, job_id="job-b",
        payload={"maintenance_run_id": "run-b"}, request_node=None,
        request_node_id="request-b", lane_message_id="",
        maintenance_kind="document_validate_crosslinks",
    )

    keys = worker._acquire_crosslink_resource_locks(first, patch)
    assert keys
    assert worker._acquire_crosslink_resource_locks(second, patch) is None
    worker._release_crosslink_resource_locks(first, keys)
    assert worker._acquire_crosslink_resource_locks(second, patch) == keys
    worker._release_crosslink_resource_locks(second, keys)


def _proposal_group(
    group_id: str,
    left_evidence_id: str = "evidence-0",
    right_evidence_id: str = "evidence-1",
) -> dict[str, object]:
    return {
        "group_id": group_id,
        "rationale": "Independent source evidence supports the relationship.",
        "indivisible": False,
        "operations": [{
            "left_evidence_id": left_evidence_id,
            "right_evidence_id": right_evidence_id,
            "relation": "supports",
            "rationale": "Both excerpts support the same claim.",
        }],
    }


def _critic_reply(
    evidence: list[CrosslinkEvidence | dict[str, object]], verdict: str
) -> dict[str, object]:
    return {
        "verdict": verdict,
        "explanation": "The cited source excerpts support this review decision.",
        "evidence_ids": [
            item["evidence_id"] if isinstance(item, dict) else item.evidence_id
            for item in evidence
        ],
    }


def test_background_provider_empty_result_finishes_without_critic_or_graph_write() -> None:
    worker, records = _fake_proposal_worker(_evidence_pair(), {"groups": []}, {})
    worker._propose_background_crosslink_groups(records["context"][0])

    assert records["finished"] == [{
        "groups": 0,
        "status": "no_candidate",
        "pending": 0,
        "automatic": 0,
        "rejected": 0,
    }]
    assert records["critic_calls"] == []
    assert records["reviews"] == []
    assert records["apply_jobs"] == []


@pytest.mark.parametrize(
    "proposal, error_match",
    [
        ({"groups": [{"group_id": "bad", "operations": []}]}, "rationale|operations"),
        ({"groups": [_proposal_group("unknown", "missing", "evidence-1")]}, "unknown evidence"),
        (
            {"groups": [{
                **_proposal_group("duplicate"),
                "operations": [
                    _proposal_group("duplicate")["operations"][0],
                    _proposal_group("duplicate")["operations"][0],
                ],
            }]},
            "duplicate operation",
        ),
    ],
)
def test_background_provider_rejects_malformed_unknown_and_duplicate_operations(
    proposal: object, error_match: str
) -> None:
    worker, records = _fake_proposal_worker(
        _evidence_pair(), proposal, _critic_reply(_evidence_pair()[:2], "approve")
    )
    worker._propose_background_crosslink_groups(records["context"][0])
    assert len(records["rejections"]) == 1
    assert error_match.replace("|", " ").split()[0] in str(records["rejections"][0]["error"])
    assert records["finished"][0]["rejected"] == 1
    assert records["apply_jobs"] == []


@pytest.mark.parametrize(
    "approval_mode, verdict, expected_status, should_enqueue",
    [
        ("automatic", "approve", "ready", True),
        ("automatic", "reject", "rejected", False),
        ("automatic", "review", "pending", False),
        ("human", "approve", "pending", False),
    ],
)
def test_provider_critic_verdict_and_approval_policy_route_independently(
    approval_mode: str,
    verdict: str,
    expected_status: str,
    should_enqueue: bool,
) -> None:
    evidence = _evidence_pair()
    worker, records = _fake_proposal_worker(
        evidence,
        {"groups": [_proposal_group("route")]},
        lambda _group, group_evidence: _critic_reply(group_evidence, verdict),
        approval_mode=approval_mode,
    )
    worker._propose_background_crosslink_groups(records["context"][0])

    assert len(records["critic_calls"]) == 1
    assert records["reviews"][0]["status"] == expected_status
    assert bool(records["apply_jobs"]) is should_enqueue
    assert records["finished"][-1]["groups"] == 1


def test_parse_quality_blocks_provider_and_crosslink_generation() -> None:
    worker, records = _fake_proposal_worker(
        _evidence_pair(), {"groups": []}, {}, parse_quality="quality_unknown"
    )
    worker._propose_background_crosslink_groups(records["context"][0])

    assert records["finished"] == [{"groups": 0, "status": "blocked_parse_quality"}]
    assert records["critic_calls"] == []
    assert records["reviews"] == []
    assert records["apply_jobs"] == []


def test_provider_groups_get_independent_critic_and_review_artifacts() -> None:
    evidence = _evidence_pair()
    worker, records = _fake_proposal_worker(
        evidence,
        {"groups": [
            _proposal_group("first", "evidence-0", "evidence-1"),
            _proposal_group("second", "evidence-2", "evidence-3"),
        ]},
        lambda _group, group_evidence: _critic_reply(group_evidence, "approve"),
        approval_mode="human",
    )
    worker._propose_background_crosslink_groups(records["context"][0])

    assert len(records["critic_calls"]) == len(records["reviews"]) == 2
    assert len(records["apply_jobs"]) == 0
    assert len({str(review["group_id"]) for review in records["reviews"]}) == 2
    assert [review["status"] for review in records["reviews"]] == ["pending", "pending"]


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
    if critic_calls:
        assert critic_calls[0]["evidence"][0]["node_id"].startswith("N")
        assert critic_calls[0]["neighbor_context"]["nodes"] == []
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

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        maintenance_messages = pipeline.engines.conversation.read.get_nodes(
            limit=500
        )
    stage_messages = [
        node for node in maintenance_messages
        if node.metadata.get("msg_type") == "maintenance.workflow.stage"
    ]
    assert stage_messages
    stage_payloads = [
        json.loads(str(node.metadata["payload_json"])) for node in stage_messages
    ]
    assert {str(item["workflow_stage"]) for item in stage_payloads} >= {
        "select", "evidence", "propose", "validate", "critic", "route",
        "pending", "outcome", "continue",
    }
    assert all(
        node.metadata.get("namespace") == ns.conv_bg
        and node.metadata.get("conversation_id") == "maintenance:provider-e2e-request"
        and node.metadata.get("purpose") == "internal"
        for node in stage_messages
    )
    assert all(
        "rationale" not in payload
        and "explanation" not in payload
        and "reasoning" not in payload
        for payload in stage_payloads
    )
    summaries = [
        node for node in maintenance_messages
        if node.metadata.get("artifact_kind") == "maintenance_run_summary"
    ]
    assert len(summaries) == 1
    summary_metadata = dict(summaries[0].metadata or {})
    assert summary_metadata["maintenance_run_id"]
    assert summary_metadata["workspace_id"] == workspace_id
    assert summary_metadata["groups_proposed"] == 1
    assert summary_metadata["groups_pending"] == 1
    assert summary_metadata["trace_persistence_complete"] is True

    # Re-emitting a transition must not create a second maintenance record.
    worker._trace_crosslink_workflow_stage(
        ctx, "pending", "pending", group_id="provider-group", artifact_id=str(pending[0].id)
    )
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        repeated_messages = pipeline.engines.conversation.read.get_nodes(
            where={"kind": "lane_message"}, limit=200
        )
    assert len([
        node for node in repeated_messages
        if node.metadata.get("msg_type") == "maintenance.workflow.stage"
        and json.loads(str(node.metadata["payload_json"])).get("workflow_stage") == "pending"
        and json.loads(str(node.metadata["payload_json"])).get("outcome") == "pending"
    ]) == 1

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
        messages = None

        def invoke(self, _messages, *, config):
            self.messages = _messages
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
            return structured

    structured = Structured()

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
            "crosslink_context_budget": {"max_nodes": 2, "max_edges": 2},
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
    worker._crosslink_prompt_context = lambda _evidence, _ctx: {
        "enabled": True,
        "nodes": [{"context_kind": "neighbor_node", "node_id": "neighbor-1"}],
        "edges": [],
        "omitted_nodes": 0,
        "omitted_edges": 0,
        "estimated_tokens": 4,
        "characters": 16,
        "budget": {"max_nodes": 2, "max_edges": 2},
    }

    result = worker._invoke_crosslink_proposer([evidence], ctx)

    assert result == {"groups": []}
    assert structured.messages is not None
    prompt = json.loads(structured.messages[1][1])
    assert prompt["neighbor_context"]["nodes"][0]["node_id"].startswith("N")
    usage = durable_maintenance_usage(
        pipeline.engines.conversation.meta_sqlite,
        namespace=WorkspaceNamespaces(ctx.workspace_id).usage_events,
        maintenance_job_id=ctx.job_id,
    )
    assert usage["call_used"] == 1
    assert usage["token_used"] == 5
