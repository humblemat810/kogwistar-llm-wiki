from kogwistar_llm_wiki import WorkspaceNamespaces


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


def test_job_and_manifest_namespaces_do_not_collide():
    ns = WorkspaceNamespaces("demo")
    assert ns.maintenance_jobs != ns.conv_bg
    assert ns.projection_jobs != ns.conv_bg
    assert ns.maintenance_jobs != ns.projection_jobs
    assert ns.derived_knowledge != ns.kg
    assert ns.derived_knowledge != ns.wisdom
    assert ns.projection_manifest != ns.maintenance_jobs
    assert ns.projection_manifest != ns.projection_jobs


def test_namespace_engines_share_one_conversation_engine(namespace_engines):
    assert namespace_engines.workflow is not namespace_engines.conversation
    assert namespace_engines.kg is not namespace_engines.conversation
    assert namespace_engines.wisdom is not namespace_engines.conversation
    assert not hasattr(namespace_engines, "review")
