from __future__ import annotations

DERIVED_KNOWLEDGE_WORKFLOW_ID = "maintenance.derived_knowledge.v1"
EXECUTION_WISDOM_WORKFLOW_ID = "maintenance.execution_wisdom.v1"

EXECUTION_WISDOM_KINDS = {
    "execution_wisdom",
    "history_wisdom",
    "distill_from_history",
    "derive_problem_solving_wisdom_from_history",
}


def normalize_maintenance_kind(maintenance_kind: str | None) -> str:
    return str(maintenance_kind or "distill").strip().lower()


def workflow_id_for_maintenance_kind(maintenance_kind: str | None) -> str:
    normalized = normalize_maintenance_kind(maintenance_kind)
    if normalized in EXECUTION_WISDOM_KINDS:
        return EXECUTION_WISDOM_WORKFLOW_ID
    return DERIVED_KNOWLEDGE_WORKFLOW_ID


def is_execution_wisdom_kind(maintenance_kind: str | None) -> bool:
    return normalize_maintenance_kind(maintenance_kind) in EXECUTION_WISDOM_KINDS
