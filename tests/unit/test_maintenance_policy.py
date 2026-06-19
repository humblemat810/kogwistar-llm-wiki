from kogwistar_llm_wiki.maintenance_policy import (
    DERIVED_KNOWLEDGE_WORKFLOW_ID,
    EXECUTION_WISDOM_WORKFLOW_ID,
    GRAPH_PATCH_APPLY_WORKFLOW_ID,
    GRAPH_PATCH_PROPOSAL_WORKFLOW_ID,
    is_graph_patch_kind,
    is_execution_wisdom_kind,
    normalize_maintenance_kind,
    workflow_id_for_maintenance_kind,
)
from kogwistar_llm_wiki.maintenance_designs import (
    build_graph_patch_apply_design,
    build_graph_patch_proposal_design,
)


def test_normalize_maintenance_kind_defaults_to_distill():
    assert normalize_maintenance_kind(None) == "distill"
    assert normalize_maintenance_kind("  Distill  ") == "distill"


def test_workflow_id_for_maintenance_kind_routes_execution_wisdom():
    assert workflow_id_for_maintenance_kind("execution_wisdom") == EXECUTION_WISDOM_WORKFLOW_ID
    assert workflow_id_for_maintenance_kind("derive_problem_solving_wisdom_from_history") == EXECUTION_WISDOM_WORKFLOW_ID
    assert workflow_id_for_maintenance_kind("distill_to_wisdom") == EXECUTION_WISDOM_WORKFLOW_ID
    assert is_execution_wisdom_kind("history_wisdom") is True


def test_workflow_id_for_maintenance_kind_defaults_to_derived_knowledge():
    assert workflow_id_for_maintenance_kind("distill") == DERIVED_KNOWLEDGE_WORKFLOW_ID
    assert is_execution_wisdom_kind("distill") is False


def test_workflow_id_for_maintenance_kind_routes_graph_patch_jobs():
    assert workflow_id_for_maintenance_kind("document_seed_graph") == GRAPH_PATCH_PROPOSAL_WORKFLOW_ID
    assert workflow_id_for_maintenance_kind("document_propose_crosslinks") == GRAPH_PATCH_PROPOSAL_WORKFLOW_ID
    assert workflow_id_for_maintenance_kind("conversation_promote_to_kg") == GRAPH_PATCH_PROPOSAL_WORKFLOW_ID
    assert workflow_id_for_maintenance_kind("graph_patch_apply") == GRAPH_PATCH_APPLY_WORKFLOW_ID
    assert is_graph_patch_kind("document_correct_parse_children") is True
    assert is_graph_patch_kind("distill") is False


def test_graph_patch_designs_are_materializable_noop_workflows():
    proposal = build_graph_patch_proposal_design()
    apply = build_graph_patch_apply_design()

    assert proposal.workflow_id == GRAPH_PATCH_PROPOSAL_WORKFLOW_ID
    assert apply.workflow_id == GRAPH_PATCH_APPLY_WORKFLOW_ID
    assert any(node.metadata.get("wf_start") for node in proposal.nodes)
    assert any(node.metadata.get("wf_op") == "noop" for node in proposal.nodes)
    assert any(node.metadata.get("wf_start") for node in apply.nodes)
    assert any(node.metadata.get("wf_op") == "noop" for node in apply.nodes)
