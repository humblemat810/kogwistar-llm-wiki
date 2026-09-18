from __future__ import annotations

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.models import Grounding, Span
from kogwistar.id_provider import stable_id
from kogwistar.runtime.models import WorkflowDesignArtifact, WorkflowEdge, WorkflowNode

from .maintenance_policy import (
    DERIVED_KNOWLEDGE_WORKFLOW_ID,
    EXECUTION_WISDOM_WORKFLOW_ID,
    GRAPH_PATCH_APPLY_WORKFLOW_ID,
    GRAPH_PATCH_PROPOSAL_WORKFLOW_ID,
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
                )
            ]
        )
    ]


def _terminal_node(workflow_id: str, *, node_id: str, label: str, summary: str) -> WorkflowNode:
    return WorkflowNode(
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
        mentions=_dummy_grounding(),
        metadata={
            "entity_type": "workflow_edge",
            "workflow_id": workflow_id,
            "wf_predicate": None,
            "wf_is_default": True,
        },
    )


def build_derived_knowledge_design(
    workflow_id: str = DERIVED_KNOWLEDGE_WORKFLOW_ID,
) -> WorkflowDesignArtifact:
    node_distill_id = str(stable_id("wf_node", workflow_id, "distill"))
    node_check_id = str(stable_id("wf_node", workflow_id, "check_done"))
    node_terminal_id = str(stable_id("wf_node", workflow_id, "done"))

    nodes = [
        WorkflowNode(
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
        WorkflowNode(
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
        WorkflowNode(
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
        WorkflowNode(
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


def materialize_maintenance_designs(workflow_engine: GraphKnowledgeEngine) -> None:
    """Saves all authoritative maintenance designs to the workflow engine."""
    for design in (
        build_derived_knowledge_design(),
        build_execution_wisdom_design(),
        build_graph_patch_proposal_design(),
        build_graph_patch_apply_design(),
    ):
        for node in design.nodes:
            workflow_engine.write.add_node(node)
        for edge in design.edges:
            workflow_engine.write.add_edge(edge)
