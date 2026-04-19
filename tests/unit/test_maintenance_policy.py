from kogwistar_llm_wiki.maintenance_policy import (
    DERIVED_KNOWLEDGE_WORKFLOW_ID,
    EXECUTION_WISDOM_WORKFLOW_ID,
    is_execution_wisdom_kind,
    normalize_maintenance_kind,
    workflow_id_for_maintenance_kind,
)


def test_normalize_maintenance_kind_defaults_to_distill():
    assert normalize_maintenance_kind(None) == "distill"
    assert normalize_maintenance_kind("  Distill  ") == "distill"


def test_workflow_id_for_maintenance_kind_routes_execution_wisdom():
    assert workflow_id_for_maintenance_kind("execution_wisdom") == EXECUTION_WISDOM_WORKFLOW_ID
    assert workflow_id_for_maintenance_kind("derive_problem_solving_wisdom_from_history") == EXECUTION_WISDOM_WORKFLOW_ID
    assert is_execution_wisdom_kind("history_wisdom") is True


def test_workflow_id_for_maintenance_kind_defaults_to_derived_knowledge():
    assert workflow_id_for_maintenance_kind("distill") == DERIVED_KNOWLEDGE_WORKFLOW_ID
    assert is_execution_wisdom_kind("distill") is False
