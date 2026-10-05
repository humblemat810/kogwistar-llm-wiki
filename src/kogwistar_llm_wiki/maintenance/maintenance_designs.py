from __future__ import annotations

from typing import Literal

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.models import Grounding, Span
from kogwistar.id_provider import stable_id
from kogwistar.runtime.models import WorkflowDesignArtifact, WorkflowEdge, WorkflowNode

from .maintenance_policy import (
    CROSSLINK_GROUP_WORKFLOW_ID,
    DERIVED_KNOWLEDGE_WORKFLOW_ID,
    EXECUTION_WISDOM_WORKFLOW_ID,
    GRAPH_PATCH_APPLY_WORKFLOW_ID,
    GRAPH_PATCH_PROPOSAL_WORKFLOW_ID,
    MULTIMODAL_RETRIEVAL_WORKFLOW_ID,
)


def _dummy_grounding() -> list[Grounding]:
    return [
        Grounding(
            spans=[
                Span(
                    doc_id="dummy",
                    start_char=0,
                    end_char=1,
                    excerpt="",
                    document_page_url="",
                    collection_page_url="",
                    insertion_method="",
                    page_number=1,
                    context_before="",
                    context_after="",
                    chunk_id=None,
                    source_cluster_id=None,
                    verification=None,
                )
            ]
        )
    ]


def _terminal_node(workflow_id: str, *, node_id: str, label: str, summary: str) -> WorkflowNode:
    return _workflow_node(
        id=node_id,
        label=label,
        type="entity",
        summary=summary,
        mentions=_dummy_grounding(),
        metadata={
            "entity_type": "workflow_node",
            "workflow_id": workflow_id,
            "wf_terminal": True,
        },
    )


def _workflow_edge(
    workflow_id: str,
    *,
    edge_key: str,
    source_id: str,
    target_id: str,
    label: str,
    summary: str,
) -> WorkflowEdge:
    return WorkflowEdge(
        id=str(stable_id("wf_edge", workflow_id, edge_key)),
        source_ids=[source_id],
        target_ids=[target_id],
        relation="workflow_transition",
        type="relationship",
        source_edge_ids=[],
        target_edge_ids=[],
        label=label,
        summary=summary,
        domain_id=None,
        canonical_entity_id=None,
        properties={},
        embedding=None,
        doc_id=None,
        mentions=_dummy_grounding(),
        metadata={
            "entity_type": "workflow_edge",
            "workflow_id": workflow_id,
            "wf_predicate": None,
            "wf_is_default": True,
        },
    )


def _workflow_node(
    *,
    id: str,
    label: str,
    type: Literal["entity", "relationship", "reference_pointer"],
    summary: str,
    mentions: list[Grounding],
    metadata: dict[str, object],
) -> WorkflowNode:
    return WorkflowNode(
        id=id,
        label=label,
        type=type,
        summary=summary,
        domain_id=None,
        canonical_entity_id=None,
        properties={},
        embedding=None,
        doc_id=None,
        level_from_root=None,
        mentions=mentions,
        metadata=metadata,
    )


def build_derived_knowledge_design(
    workflow_id: str = DERIVED_KNOWLEDGE_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    node_distill_id = str(stable_id("wf_node", workflow_id, "distill"))
    node_check_id = str(stable_id("wf_node", workflow_id, "check_done"))
    node_terminal_id = str(stable_id("wf_node", workflow_id, "done"))

    nodes = [
        _workflow_node(
            id=node_distill_id,
            label="Derive Knowledge Synthesis",
            type="entity",
            summary="Aggregate promoted knowledge into derived-knowledge artifacts.",
            mentions=_dummy_grounding(),
            metadata={
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "distill",
                "wf_start": True,
                "default_context_window": 4000,
            },
        ),
        _workflow_node(
            id=node_check_id,
            label="Check Derived Knowledge Complete",
            type="entity",
            summary="Confirm derived-knowledge synthesis is ready to finalize.",
            mentions=_dummy_grounding(),
            metadata={
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "check_done",
                "default_context_window": 4000,
            },
        ),
        _terminal_node(
            workflow_id,
            node_id=node_terminal_id,
            label="Derived Knowledge Complete",
            summary="Terminal state for derived-knowledge synthesis.",
        ),
    ]

    edges = [
        _workflow_edge(
            workflow_id,
            edge_key="distill_to_done",
            source_id=node_distill_id,
            target_id=node_check_id,
            label="check",
            summary="Derived-knowledge synthesis ready for completion check.",
        ),
        _workflow_edge(
            workflow_id,
            edge_key="check_to_done",
            source_id=node_check_id,
            target_id=node_terminal_id,
            label="finished",
            summary="Derived-knowledge synthesis complete.",
        )
    ]

    return WorkflowDesignArtifact(
        workflow_id=workflow_id,
        workflow_version="v1",
        start_node_id=node_distill_id,
        nodes=nodes,
        edges=edges,
    )


def build_execution_wisdom_design(
    workflow_id: str = EXECUTION_WISDOM_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    node_extract_id = str(
        stable_id("wf_node", workflow_id, "derive_problem_solving_wisdom_from_history")
    )
    node_terminal_id = str(stable_id("wf_node", workflow_id, "done"))

    nodes = [
        _workflow_node(
            id=node_extract_id,
            label="Derive Problem-Solving Wisdom From History",
            type="entity",
            summary="Read workflow failure history and emit execution-wisdom artifacts.",
            mentions=_dummy_grounding(),
            metadata={
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "derive_problem_solving_wisdom_from_history",
                "wf_start": True,
                "default_context_window": 4000,
            },
        ),
        _terminal_node(
            workflow_id,
            node_id=node_terminal_id,
            label="Execution Wisdom Complete",
            summary="Terminal state for execution-history wisdom extraction.",
        ),
    ]

    edges = [
        _workflow_edge(
            workflow_id,
            edge_key="extract_to_done",
            source_id=node_extract_id,
            target_id=node_terminal_id,
            label="finished",
            summary="Execution-wisdom extraction complete.",
        )
    ]

    return WorkflowDesignArtifact(
        workflow_id=workflow_id,
        workflow_version="v1",
        start_node_id=node_extract_id,
        nodes=nodes,
        edges=edges,
    )


def build_distillation_design(
    workflow_id: str = DERIVED_KNOWLEDGE_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    """Compatibility alias for older imports/tests."""
    return build_derived_knowledge_design(workflow_id=workflow_id)


def build_graph_patch_design(
    workflow_id: str,
    *,
    label: str,
    summary: str,
) -> WorkflowDesignArtifact:
    node_noop_id = str(stable_id("wf_node", workflow_id, "noop"))
    node_terminal_id = str(stable_id("wf_node", workflow_id, "done"))

    nodes = [
        _workflow_node(
            id=node_noop_id,
            label=label,
            type="entity",
            summary=summary,
            mentions=_dummy_grounding(),
            metadata={
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "noop",
                "wf_start": True,
                "default_context_window": 4000,
            },
        ),
        _terminal_node(
            workflow_id,
            node_id=node_terminal_id,
            label=f"{label} Complete",
            summary=f"Terminal state for {summary.lower()}",
        ),
    ]

    edges = [
        _workflow_edge(
            workflow_id,
            edge_key="noop_to_done",
            source_id=node_noop_id,
            target_id=node_terminal_id,
            label="finished",
            summary="Graph patch workflow completed.",
        )
    ]

    return WorkflowDesignArtifact(
        workflow_id=workflow_id,
        workflow_version="v1",
        start_node_id=node_noop_id,
        nodes=nodes,
        edges=edges,
    )


def build_graph_patch_proposal_design(
    workflow_id: str = GRAPH_PATCH_PROPOSAL_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    return build_graph_patch_design(
        workflow_id,
        label="Graph Patch Proposal",
        summary="Validate and prepare graph-patch proposal jobs.",
    )


def build_graph_patch_apply_design(
    workflow_id: str = GRAPH_PATCH_APPLY_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    return build_graph_patch_design(
        workflow_id,
        label="Graph Patch Apply",
        summary="Apply validated graph-patch jobs.",
    )


def build_crosslink_group_design(
    workflow_id: str = CROSSLINK_GROUP_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    """Graph-native lifecycle map for provider-backed cross-link groups."""
    stages = (
        ("select", "Select Bounded Candidates", "Select active, authorized workspace concepts."),
        ("evidence", "Assemble Source Evidence", "Resolve immutable revisions and exact source spans."),
        ("propose", "Propose Cross-Link Groups", "Generate bounded groups using supplied evidence IDs only."),
        ("validate", "Validate Groups", "Check endpoints, scope, ACL, ParseView, and grounding."),
        ("critic", "Critic Review Per Group", "Review each group independently; store verdict and citations."),
        ("route", "Route By Approval Policy", "Require critic approval for automatic application or human review."),
        ("pending", "Persist Pending Review", "Persist a workspace-scoped evaluation artifact and continue exploration."),
        ("apply", "Apply Approved Group", "Apply each accepted group as its own patch."),
        ("outcome", "Record Group Outcome", "Record applied, rejected, partial, stale, or review-required outcome."),
        ("continue", "Continue Exploration", "Continue bounded maintenance independently of pending decisions."),
    )
    nodes: list[WorkflowNode] = []
    for index, (key, label, summary) in enumerate(stages):
        metadata: dict[str, object] = {
            "entity_type": "workflow_node",
            "workflow_id": workflow_id,
            "wf_op": f"crosslink_{key}",
            "default_context_window": 4000,
        }
        if index == 0:
            metadata["wf_start"] = True
        if key == "continue":
            metadata["wf_terminal"] = True
        nodes.append(_workflow_node(
            id=str(stable_id("wf_node", workflow_id, key)),
            label=label,
            type="entity",
            summary=summary,
            mentions=_dummy_grounding(),
            metadata=metadata,
        ))
    ids = {key: str(stable_id("wf_node", workflow_id, key)) for key, _, _ in stages}
    transitions = (
        ("select", "evidence", "selected"),
        ("evidence", "propose", "evidence_ready"),
        ("propose", "validate", "groups_proposed"),
        ("validate", "critic", "deterministic_checks_passed"),
        ("critic", "route", "critic_verdict_recorded"),
        ("route", "pending", "human_review_required"),
        ("route", "apply", "automatic_approval_or_human_approval"),
        ("critic", "pending", "critic_review_required"),
        ("pending", "continue", "pending_is_non_blocking"),
        ("apply", "outcome", "patch_result_recorded"),
        ("outcome", "continue", "group_finished"),
        ("propose", "continue", "no_candidate"),
        ("validate", "continue", "rejected_or_stale"),
        ("critic", "continue", "critic_rejected"),
    )
    edges = [
        _workflow_edge(
            workflow_id,
            edge_key=f"{source}_{target}_{label}",
            source_id=ids[source],
            target_id=ids[target],
            label=label,
            summary=f"Cross-link lifecycle: {label.replace('_', ' ')}.",
        )
        for source, target, label in transitions
    ]
    return WorkflowDesignArtifact(
        workflow_id=workflow_id,
        workflow_version="v1",
        start_node_id=ids["select"],
        nodes=nodes,
        edges=edges,
    )


def build_multimodal_retrieval_design(
    workflow_id: str = MULTIMODAL_RETRIEVAL_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    """Describe the sidecar lifecycle using ordinary workflow graph entities.

    The sidecar remains an application retrieval adapter, not a second graph.
    This design is the durable workflow contract that records where the
    sidecar may overlap the text/graph path and where evidence is authorized
    before assimilation.
    """

    stages = (
        ("dispatch", "Dispatch Retrieval", "Create a scoped multimodal retrieval run."),
        ("text_graph", "Run Text And Graph Retrieval", "Continue the primary path without waiting for media."),
        ("sidecar", "Run Multimodal Sidecar", "Retrieve profile-bound references asynchronously."),
        ("checkpoint", "Reach Assimilation Checkpoint", "Pause only at an explicit worker checkpoint."),
        ("authorize", "Authorize Current Evidence", "Recheck namespace, ACL, revision, and embedding profile."),
        ("assimilate", "Assimilate Typed Evidence", "Add authorized references without mutating canonical truth."),
        ("degraded", "Record Retrieval Degradation", "Record timeout, stale, unavailable, or unauthorized evidence."),
        ("finalize", "Finalize Retrieval Run", "Close the sidecar and publish the bounded outcome."),
    )
    ids = {key: str(stable_id("wf_node", workflow_id, key)) for key, _, _ in stages}
    nodes: list[WorkflowNode] = []
    for key, label, summary in stages:
        metadata: dict[str, object] = {
            "entity_type": "workflow_node",
            "workflow_id": workflow_id,
            "wf_op": f"multimodal_{key}",
            "default_context_window": 4000,
        }
        if key == "dispatch":
            metadata["wf_start"] = True
        if key == "sidecar":
            metadata["wf_fanout"] = True
        if key == "finalize":
            metadata["wf_terminal"] = True
        nodes.append(
            _workflow_node(
                id=ids[key],
                label=label,
                type="entity",
                summary=summary,
                mentions=_dummy_grounding(),
                metadata=metadata,
            )
        )
    transitions = (
        ("dispatch", "text_graph", "primary_path"),
        ("dispatch", "sidecar", "sidecar_started"),
        ("text_graph", "checkpoint", "primary_path_ready"),
        ("sidecar", "checkpoint", "evidence_available"),
        ("sidecar", "degraded", "timeout_or_provider_failure"),
        ("checkpoint", "authorize", "checkpoint_reached"),
        ("authorize", "assimilate", "evidence_authorized"),
        ("authorize", "degraded", "evidence_rejected_or_stale"),
        ("assimilate", "finalize", "evidence_assimilated"),
        ("degraded", "finalize", "degradation_recorded"),
    )
    edges = [
        _workflow_edge(
            workflow_id,
            edge_key=f"{source}_{target}_{label}",
            source_id=ids[source],
            target_id=ids[target],
            label=label,
            summary=f"Multimodal retrieval lifecycle: {label.replace('_', ' ')}.",
        )
        for source, target, label in transitions
    ]
    return WorkflowDesignArtifact(
        workflow_id=workflow_id,
        workflow_version="v1",
        start_node_id=ids["dispatch"],
        nodes=nodes,
        edges=edges,
    )


def materialize_maintenance_designs(workflow_engine: GraphKnowledgeEngine) -> None:
    """Saves all authoritative maintenance designs to the workflow engine."""
    for design in (
        build_derived_knowledge_design(),
        build_execution_wisdom_design(),
        build_graph_patch_proposal_design(),
        build_graph_patch_apply_design(),
        build_crosslink_group_design(),
        build_multimodal_retrieval_design(),
    ):
        for node in design.nodes:
            workflow_engine.write.add_node(node)
        for edge in design.edges:
            workflow_engine.write.add_edge(edge)
