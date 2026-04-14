from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


def test_workspace_namespaces_match_docs():
    ns = WorkspaceNamespaces("demo")
    assert ns.conv_fg == "ws:demo:conv:fg"
    assert ns.conv_bg == "ws:demo:conv:bg"
    assert ns.workflow_maintenance == "ws:demo:wf:maintenance"
    assert ns.review == "ws:demo:review"
    assert ns.kg == "ws:demo:kg"
    assert ns.wisdom == "ws:demo:wisdom"
