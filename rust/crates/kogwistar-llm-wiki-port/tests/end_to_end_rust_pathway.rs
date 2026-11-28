use std::collections::BTreeMap;

use serde_json::json;

use kogwistar_llm_wiki_port::cli::parse_cli_with_env;
use kogwistar_llm_wiki_port::cli_runtime::{run_cli, InMemoryCliRuntime};
use kogwistar_llm_wiki_port::mcp_runtime::InMemoryMcpGateway;
use kogwistar_llm_wiki_port::mcp_server::{dispatch, tool_specs};

#[test]
fn cli_report_flows_into_mcp_status_without_python() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("source.md");
    std::fs::write(&source, "alpha beta gamma").unwrap();
    let vault = temp.path().join("vault");
    std::fs::create_dir_all(&vault).unwrap();
    let source_path = source.to_string_lossy().replace('\\', "/");
    let vault_path = vault.to_string_lossy().replace('\\', "/");
    let environment = BTreeMap::new();
    let strings = |values: &[&str]| {
        values
            .iter()
            .map(|value| value.to_string())
            .collect::<Vec<_>>()
    };
    let mut runtime = InMemoryCliRuntime::default();

    let demo = run_cli(
        &parse_cli_with_env(
            &strings(&[
                "--data-dir",
                "data",
                "demo",
                "--workspace",
                "ws",
                "--source",
                &source_path,
                "--vault",
                &vault_path,
            ]),
            &environment,
        )
        .unwrap(),
        &mut runtime,
    )
    .unwrap();
    assert_eq!(demo["mode"], "demo-memory-single-process");

    let report = run_cli(
        &parse_cli_with_env(
            &strings(&[
                "--data-dir",
                "data",
                "report",
                "--workspace",
                "ws",
                "--data-dir",
                "data",
            ]),
            &environment,
        )
        .unwrap(),
        &mut runtime,
    )
    .unwrap();
    assert_eq!(report["graph_spaces"][0]["node_count"], 1);

    let gateway = InMemoryMcpGateway::new(runtime);
    assert_eq!(tool_specs().unwrap().len(), 18);
    let status = dispatch("status", &json!({"workspace_id": "ws"}), &gateway).unwrap();
    assert_eq!(status["graph_spaces"][0]["node_count"], 1);
    assert_eq!(status["maintenance_patch_report"]["patch_count"], 0);
}
