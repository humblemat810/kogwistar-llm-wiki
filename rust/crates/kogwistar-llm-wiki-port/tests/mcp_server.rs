use kogwistar_llm_wiki_port::mcp_server::tool_specs;

#[test]
fn exposes_all_python_tool_names_and_schemas() {
    let specs = tool_specs().unwrap();
    let names: Vec<&str> = specs.iter().map(|spec| spec.name.as_str()).collect();
    assert_eq!(
        names,
        vec![
            "query",
            "search",
            "ingest",
            "source",
            "reingest",
            "maintain",
            "status",
            "hypergraph_search",
            "history",
            "memory_recall",
            "memory_capture",
            "memory_review",
            "multimodal_capture",
            "multimodal_index",
            "multimodal_search",
            "multimodal_status",
            "propose",
            "confirm",
        ]
    );
    assert_eq!(specs.len(), 18);
    let query = specs.iter().find(|spec| spec.name == "query").unwrap();
    assert_eq!(query.input_schema["type"], "object");
    assert_eq!(query.input_schema["required"][0], "workspace_id");
    assert_eq!(
        query.input_schema["properties"]["query_text"]["type"],
        "string"
    );
}
