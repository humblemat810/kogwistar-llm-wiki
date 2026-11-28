use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::cli::parse_cli_with_env;
use kogwistar_llm_wiki_port::cli_runtime::{run_cli, InMemoryCliRuntime};
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
fn in_memory_runtime_ingests_and_reports_without_python() {
    let mut runtime = InMemoryCliRuntime::default();
    let document = runtime.ingest(request()).unwrap();
    assert_eq!(document.source_unit.workspace_id, "ws");
    assert_eq!(document.artifacts.operation_mode, "parse_first");
    assert_eq!(document.artifacts.graph_status, "stable");

    let report = runtime.report("ws", "all", "summary", 20).unwrap();
    assert_eq!(report["workspace_id"], "ws");
    assert_eq!(report["report_scope"], "all");
    assert_eq!(report["dump_mode"], "summary");
    assert_eq!(report["maintenance_patch_report"]["patch_count"], 0);
    assert_eq!(report["graph_spaces"][0]["graph_space"], "base_kg");
    assert_eq!(report["graph_spaces"][0]["node_count"], 1);
}

#[test]
fn dispatches_ingest_command_to_runtime() {
    let mut runtime = InMemoryCliRuntime::default();
    let invocation = parse_cli_with_env(
        &[
            "--data-dir",
            "data",
            "ingest",
            "--workspace",
            "ws",
            "--source",
            "does-not-exist.md",
        ]
        .iter()
        .map(|value| value.to_string())
        .collect::<Vec<_>>(),
        &BTreeMap::new(),
    )
    .unwrap();
    let error = run_cli(&invocation, &mut runtime).unwrap_err();
    assert_eq!(
        error.to_string(),
        "source file not found: does-not-exist.md"
    );
}

#[test]
fn dispatches_file_demo_and_report_happy_paths() {
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("source.md");
    std::fs::write(&source, "alpha beta gamma").unwrap();
    let vault = temp.path().join("vault");
    std::fs::create_dir_all(&vault).unwrap();
    let source_path = source.to_string_lossy().replace('\\', "/");
    let vault_path = vault.to_string_lossy().replace('\\', "/");
    let mut runtime = InMemoryCliRuntime::default();
    let environment = BTreeMap::new();
    let strings = |values: &[&str]| {
        values
            .iter()
            .map(|value| value.to_string())
            .collect::<Vec<_>>()
    };

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
                "--source-format",
                "markdown",
            ]),
            &environment,
        )
        .unwrap(),
        &mut runtime,
    )
    .unwrap();
    assert_eq!(demo["workspace_id"], "ws");
    assert_eq!(demo["mode"], "demo-memory-single-process");
    assert_eq!(demo["corpus_count"], 1);
    assert_eq!(demo["artifacts"]["graph_status"], "stable");

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
                "--report-scope",
                "all",
                "--dump-mode",
                "summary",
            ]),
            &environment,
        )
        .unwrap(),
        &mut runtime,
    )
    .unwrap();
    assert_eq!(report["workspace_id"], "ws");
    assert_eq!(report["graph_spaces"][0]["node_count"], 1);
    assert_eq!(report["maintenance_patch_report"]["patch_count"], 0);
}
