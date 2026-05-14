from __future__ import annotations

from kogwistar_llm_wiki.policies import LlmWikiPolicies


def test_llm_wiki_taxonomy_maps_visibility_and_projection():
    policies = LlmWikiPolicies()
    taxonomy = policies.taxonomy

    assert policies.visibility.visibility_for({"artifact_kind": taxonomy.candidate_link}) == "review"
    assert policies.visibility.visibility_for({"artifact_kind": taxonomy.promotion_candidate}) == "review"
    assert policies.visibility.visibility_for({"artifact_kind": taxonomy.promoted_knowledge}) == "knowledge"
    assert policies.visibility.visibility_for(
        {
            "artifact_kind": taxonomy.promoted_knowledge,
            "projection_visible": True,
        }
    ) == "projection"
    assert policies.projection.is_projection_eligible(
        {
            "artifact_kind": taxonomy.promoted_knowledge,
            "projection_visible": True,
        }
    )


def test_llm_wiki_taxonomy_drives_source_and_match_queries():
    policies = LlmWikiPolicies()
    taxonomy = policies.taxonomy

    assert policies.derived_knowledge.source_query(workspace_id="w1").where == {
        "artifact_kind": taxonomy.promoted_knowledge,
        "workspace_id": "w1",
    }
    assert policies.derived_knowledge.match_where(
        workspace_id="w1",
        label="Entity",
    ) == {
        "artifact_kind": taxonomy.derived_knowledge,
        "workspace_id": "w1",
        "label": "Entity",
    }
    assert policies.wisdom.source_query(workspace_id="w1").where == {
        "entity_type": taxonomy.workflow_step_exec_entity_type,
        "workspace_id": "w1",
    }
    assert policies.wisdom.match_where(workspace_id="w1", step_op="distill") == {
        "artifact_kind": taxonomy.execution_wisdom,
        "workspace_id": "w1",
        "step_op": "distill",
    }


def test_llm_wiki_lifecycle_policy_owns_app_artifact_names():
    policies = LlmWikiPolicies()
    taxonomy = policies.taxonomy

    assert policies.lifecycle.requires_provenance(taxonomy.promoted_knowledge)
    assert policies.lifecycle.requires_provenance(taxonomy.derived_knowledge)
    assert policies.lifecycle.requires_provenance(taxonomy.execution_wisdom)
    assert not policies.lifecycle.requires_provenance(taxonomy.candidate_link)
