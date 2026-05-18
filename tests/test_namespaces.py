from kogwistar_llm_wiki import GraphSpace, GraphSpaceNamespace, WorkspaceNamespaces
from kogwistar_llm_wiki.namespaces import namespace_matches_graph_space_metadata


def test_namespaces_include_wisdom_and_review():
    ns = WorkspaceNamespaces("demo")
    assert ns.conv_fg == "ws:demo:conv:fg"
    assert ns.conv_bg == "ws:demo:conv:bg"
    assert ns.workflow_maintenance == "ws:demo:wf:maintenance"
    assert ns.review == "ws:demo:review"
    assert ns.kg == "ws:demo:kg"
    assert ns.derived_knowledge == "ws:demo:kg:derived"
    assert ns.wisdom == "ws:demo:wisdom"
    assert ns.maintenance_jobs == "ws:demo:maintenance_jobs"
    assert ns.projection_jobs == "ws:demo:projection_jobs"
    assert ns.projection_manifest == "ws:demo:projection_manifest"
    assert ns.projection_state == "ws:demo:projection_state"
    assert ns.source_space == "ws:demo:g:source"
    assert ns.base_kg_space == "ws:demo:g:base_kg"
    assert ns.curated_kg_space == "ws:demo:g:curated_kg"
    assert ns.conversation_fg_space == "ws:demo:g:conversation:lane:foreground"
    assert ns.conversation_bg_space == "ws:demo:g:conversation:lane:background"
    assert ns.workflow_space == "ws:demo:g:workflow"
    assert ns.review_space == "ws:demo:g:review"
    assert ns.wisdom_space == "ws:demo:g:wisdom"
    assert ns.policy_space == "ws:demo:g:policy"
    assert ns.projection_space == "ws:demo:g:projection"


def test_graph_space_namespace_serializes_and_emits_metadata():
    ns = GraphSpaceNamespace("demo", GraphSpace.SOURCE)
    assert ns.as_string() == "ws:demo:g:source"
    assert ns.metadata() == {"workspace_id": "demo", "graph_space": "source"}

    lane_ns = GraphSpaceNamespace("demo", GraphSpace.CONVERSATION, lane="foreground")
    assert lane_ns.as_string() == "ws:demo:g:conversation:lane:foreground"
    assert lane_ns.metadata(legacy_namespace="ws:demo:conv:fg") == {
        "workspace_id": "demo",
        "graph_space": "conversation",
        "graph_lane": "foreground",
        "legacy_namespace": "ws:demo:conv:fg",
    }


def test_job_and_manifest_namespaces_do_not_collide():
    ns = WorkspaceNamespaces("demo")
    assert ns.maintenance_jobs != ns.conv_bg
    assert ns.projection_jobs != ns.conv_bg
    assert ns.maintenance_jobs != ns.projection_jobs
    assert ns.derived_knowledge != ns.kg
    assert ns.derived_knowledge != ns.wisdom
    assert ns.projection_manifest != ns.maintenance_jobs
    assert ns.projection_manifest != ns.projection_jobs


def test_namespace_matches_graph_space_metadata():
    assert namespace_matches_graph_space_metadata(
        "ws:demo:g:source",
        {"workspace_id": "demo", "graph_space": "source"},
    )
    assert namespace_matches_graph_space_metadata(
        "ws:demo:g:conversation:lane:foreground",
        {"workspace_id": "demo", "graph_space": "conversation", "graph_lane": "foreground"},
    )
    assert not namespace_matches_graph_space_metadata(
        "ws:demo:g:source",
        {"workspace_id": "demo", "graph_space": "curated_kg"},
    )
    assert not namespace_matches_graph_space_metadata(
        "ws:demo:g:conversation:lane:background",
        {"workspace_id": "demo", "graph_space": "conversation", "graph_lane": "foreground"},
    )
    assert not namespace_matches_graph_space_metadata(
        "ws:demo:conv:fg",
        {"workspace_id": "demo", "graph_space": "conversation", "graph_lane": "foreground"},
    )


def test_namespace_engines_share_one_conversation_engine(namespace_engines):
    assert namespace_engines.workflow is not namespace_engines.conversation
    assert namespace_engines.kg is not namespace_engines.conversation
    assert namespace_engines.wisdom is not namespace_engines.conversation
    assert namespace_engines.derived_knowledge_engine() is namespace_engines.kg
    assert not hasattr(namespace_engines, "review")
