from __future__ import annotations

DERIVED_KNOWLEDGE_WORKFLOW_ID = "maintenance.derived_knowledge.v1"
EXECUTION_WISDOM_WORKFLOW_ID = "maintenance.execution_wisdom.v1"
GRAPH_PATCH_PROPOSAL_WORKFLOW_ID = "maintenance.graph_patch_proposal.v1"
GRAPH_PATCH_APPLY_WORKFLOW_ID = "maintenance.graph_patch_apply.v1"

EXECUTION_WISDOM_KINDS = {
    "execution_wisdom",
    "history_wisdom",
    "distill_from_history",
    "derive_problem_solving_wisdom_from_history",
    "distill_to_wisdom",
}

GRAPH_PATCH_PROPOSAL_KINDS = {
    "document_seed_graph",
    "document_parse_graph",
    "document_expand_parse_children",
    "document_correct_parse_children",
    "document_summarize_units",
    "document_extract_entities",
    "document_propose_crosslinks",
    "document_validate_crosslinks",
    "document_retract_crosslinks",
    "document_detect_conflicts",
    "entity_merge_candidate",
    "entity_disambiguation_scan",
    "entity_disambiguation_reconcile",
    "entity_disambiguation_review",
    "entity_disambiguation_patch_proposal",
    "conversation_promote_to_kg",
    "graph_patch_review",
}

DOCUMENT_PARSE_KINDS = {"document_parse_graph"}

GRAPH_PATCH_APPLY_KINDS = {
    "graph_patch_apply",
}


def normalize_maintenance_kind(maintenance_kind: str | None) -> str:
    return str(maintenance_kind or "distill").strip().lower()


def workflow_id_for_maintenance_kind(maintenance_kind: str | None) -> str:
    normalized = normalize_maintenance_kind(maintenance_kind)
    if normalized in EXECUTION_WISDOM_KINDS:
        return EXECUTION_WISDOM_WORKFLOW_ID
    if normalized in GRAPH_PATCH_APPLY_KINDS:
        return GRAPH_PATCH_APPLY_WORKFLOW_ID
    if normalized in GRAPH_PATCH_PROPOSAL_KINDS:
        return GRAPH_PATCH_PROPOSAL_WORKFLOW_ID
    return DERIVED_KNOWLEDGE_WORKFLOW_ID


def is_execution_wisdom_kind(maintenance_kind: str | None) -> bool:
    return normalize_maintenance_kind(maintenance_kind) in EXECUTION_WISDOM_KINDS


def is_graph_patch_kind(maintenance_kind: str | None) -> bool:
    normalized = normalize_maintenance_kind(maintenance_kind)
    return normalized in GRAPH_PATCH_PROPOSAL_KINDS or normalized in GRAPH_PATCH_APPLY_KINDS
