from __future__ import annotations

from kogwistar.id_provider import stable_id
from kogwistar.runtime.models import (
    WorkflowDesignArtifact,
    WorkflowEdge,
    WorkflowNode,
)
from kogwistar.engine_core.models import Grounding, Span


def build_distillation_design(workflow_id: str = "maintenance.distillation.v1") -> WorkflowDesignArtifact:
    """
    Builds a looping graph-native design for distillation.
    Flow: start -> distill -> check_done -> (if continue) -> distill
                                         -> (if done) -> done
    """
    node_distill_id = str(stable_id("wf_node", workflow_id, "distill"))
    node_check_id = str(stable_id("wf_node", workflow_id, "check_done"))
    node_terminal_id = str(stable_id("wf_node", workflow_id, "done"))

    nodes = [
        WorkflowNode(
            id=node_distill_id,
            label="Distill Wisdom",
            type="entity",
            summary="Extract reusable lessons from ingestion artifacts.",
            mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
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
            label="Check Progress",
            type="entity",
            summary="Decide if more distillation passes are needed.",
            mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
            metadata={
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_op": "check_done",
            },
        ),
        WorkflowNode(
            id=node_terminal_id,
            label="Design Complete",
            type="entity",
            summary="Terminal state for distillation.",
            mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
            metadata={
                "entity_type": "workflow_node",
                "workflow_id": workflow_id,
                "wf_terminal": True,
            },
        ),
    ]

    edges = [
        WorkflowEdge(
            id=str(stable_id("wf_edge", workflow_id, "distill_to_check")),
            source_ids=[node_distill_id],
            target_ids=[node_check_id],
            relation="workflow_transition",
            type="relationship",
            source_edge_ids=[],
            target_edge_ids=[],
            label="to_check",
            summary="Check if finished.",
            mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
            metadata={
                "entity_type": "workflow_edge",
                "workflow_id": workflow_id,
                "wf_predicate": None,
            },
        ),
        WorkflowEdge(
            id=str(stable_id("wf_edge", workflow_id, "check_to_distill")),
            source_ids=[node_check_id],
            target_ids=[node_distill_id],
            relation="workflow_transition",
            type="relationship",
            source_edge_ids=[],
            target_edge_ids=[],
            label="loop",
            summary="Next pass.",
            mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
            metadata={
                "entity_type": "workflow_edge",
                "workflow_id": workflow_id,
                "wf_predicate": "continue",
            },
        ),
        WorkflowEdge(
            id=str(stable_id("wf_edge", workflow_id, "check_to_done")),
            source_ids=[node_check_id],
            target_ids=[node_terminal_id],
            relation="workflow_transition",
            type="relationship",
            source_edge_ids=[],
            target_edge_ids=[],
            label="finished",
            summary="Distillation complete.",
            mentions=[Grounding(spans=[Span(doc_id="dummy", start_char=0, end_char=1, excerpt="", document_page_url="", collection_page_url="", insertion_method="")])],
            metadata={
                "entity_type": "workflow_edge",
                "workflow_id": workflow_id,
                "wf_predicate": None,
                "wf_is_default": True,
            },
        ),
    ]

    return WorkflowDesignArtifact(
        workflow_id=workflow_id,
        workflow_version="v1",
        start_node_id=node_distill_id,
        nodes=nodes,
        edges=edges,
    )


def materialize_maintenance_designs(workflow_engine: any):
    """Saves all authoritative maintenance designs to the workflow engine."""
    design = build_distillation_design()
    for node in design.nodes:
        workflow_engine.write.add_node(node)
    for edge in design.edges:
        workflow_engine.write.add_edge(edge)
