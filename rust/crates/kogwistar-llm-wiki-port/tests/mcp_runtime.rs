use std::collections::BTreeMap;

use serde_json::json;

use kogwistar_llm_wiki_port::cli_runtime::InMemoryCliRuntime;
use kogwistar_llm_wiki_port::mcp_runtime::InMemoryMcpGateway;
use kogwistar_llm_wiki_port::mcp_server::dispatch;
use kogwistar_llm_wiki_port::models::IngestPipelineRequest;

fn request() -> IngestPipelineRequest {
    IngestPipelineRequest {
        workspace_id: "ws".to_string(),
        source_uri: "file:///source.md".to_string(),
        title: "Source".to_string(),
        raw_text: "alpha beta gamma".to_string(),
        source_format: "markdown".to_string(),
        operation_mode: "parse_first".to_string(),
        parser_mode: "heuristic".to_string(),
        parser_lane: "page_index".to_string(),
        promotion_mode: "sync".to_string(),
        auto_accept_threshold: 0.85,
        llm_provider: None,
        llm_model: None,
        provenance_policy: kogwistar_llm_wiki_port::models::ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: BTreeMap::new(),
    }
}

#[test]
fn status_tool_runs_end_to_end_without_python() {
    let mut runtime = InMemoryCliRuntime::default();
    runtime.ingest(request()).unwrap();
    let gateway = InMemoryMcpGateway::new(runtime);
    let result = dispatch("status", &json!({"workspace_id": "ws"}), &gateway).unwrap();
    assert_eq!(result["workspace_id"], "ws");
    assert_eq!(result["graph_spaces"][0]["node_count"], 1);
}

#[test]
fn query_tool_returns_grounded_absence_without_python() {
    let runtime = InMemoryCliRuntime::default();
    let gateway = InMemoryMcpGateway::new(runtime);
    let result = dispatch(
        "query",
        &json!({"workspace_id": "ws", "query_text": "alpha"}),
        &gateway,
    )
    .unwrap();
    assert_eq!(result["answer"]["text"], "No matching knowledge yet.");
    assert_eq!(result["answer"]["workspace_id"], "ws");
}
