use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::cli::{
    parse_cli_with_env, CliError, ConversationPersistenceMode, VectorBackend,
};

fn arguments(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| (*value).to_string()).collect()
}

#[test]
fn routes_core_commands_and_handler_names() {
    let environment = BTreeMap::new();
    let cases: Vec<(&str, Vec<&str>, &str)> = vec![
        (
            "demo",
            vec!["demo", "--workspace", "ws", "--source", "a", "--vault", "v"],
            "demo",
        ),
        (
            "ingest",
            vec!["ingest", "--workspace", "ws", "--source", "a"],
            "ingest",
        ),
        (
            "report",
            vec!["report", "--workspace", "ws", "--data-dir", "data"],
            "report",
        ),
        (
            "workbench",
            vec!["workbench", "--workspace", "ws"],
            "workbench",
        ),
        ("mcp", vec!["mcp", "--workspace", "ws"], "mcp"),
        ("serve", vec!["serve", "--workspace", "ws"], "serve"),
        (
            "embedding-service",
            vec!["embedding-service"],
            "embedding_service",
        ),
        (
            "seed-bundle",
            vec![
                "seed-bundle",
                "--workspace",
                "ws",
                "--bundle",
                "b",
                "--output",
                "o",
            ],
            "seed_bundle",
        ),
    ];
    for (_, command_arguments, handler) in cases {
        let invocation = parse_cli_with_env(&arguments(&command_arguments), &environment).unwrap();
        assert_eq!(invocation.command.handler_name(), handler);
    }
}

#[test]
fn preserves_required_and_optional_arguments() {
    let environment = BTreeMap::new();
    let invocation = parse_cli_with_env(
        &arguments(&[
            "demo",
            "--workspace",
            "ws",
            "--source",
            "a.md",
            "--vault",
            "vault",
            "--title",
            "Title",
            "--source-format",
            "markdown",
            "--parser-mode",
            "heuristic",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(
        invocation.arguments.get("workspace").map(String::as_str),
        Some("ws")
    );
    assert_eq!(
        invocation.arguments.get("source").map(String::as_str),
        Some("a.md")
    );
    assert_eq!(
        invocation.arguments.get("vault").map(String::as_str),
        Some("vault")
    );
    assert_eq!(
        invocation.arguments.get("title").map(String::as_str),
        Some("Title")
    );
    assert_eq!(
        invocation
            .arguments
            .get("source_format")
            .map(String::as_str),
        Some("markdown")
    );
    assert_eq!(
        invocation.arguments.get("parser_mode").map(String::as_str),
        Some("heuristic")
    );
}

#[test]
fn rejects_missing_required_or_unknown_command_arguments() {
    let environment = BTreeMap::new();
    let missing =
        parse_cli_with_env(&arguments(&["report", "--workspace", "ws"]), &environment).unwrap_err();
    assert_eq!(missing, CliError::MissingArgument("data_dir".to_string()));
    let unknown = parse_cli_with_env(
        &arguments(&[
            "report",
            "--workspace",
            "ws",
            "--data-dir",
            "data",
            "--unknown",
            "x",
        ]),
        &environment,
    )
    .unwrap_err();
    assert_eq!(unknown, CliError::UnknownCommand("--unknown".to_string()));
}

#[test]
fn parses_boolean_subcommand_flags_without_values() {
    let environment = BTreeMap::new();
    let invocation = parse_cli_with_env(
        &arguments(&[
            "report",
            "--workspace",
            "ws",
            "--data-dir",
            "data",
            "--split-derived-knowledge",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(
        invocation
            .arguments
            .get("split_derived_knowledge")
            .map(String::as_str),
        Some("true")
    );
}

#[test]
fn parses_global_settings_from_explicit_flags() {
    let environment = BTreeMap::new();
    let invocation = parse_cli_with_env(
        &arguments(&[
            "--data-dir",
            "data",
            "--backend",
            "postgres",
            "--dsn",
            "postgres://example",
            "--split-derived-knowledge",
            "--conversation-persistence-mode",
            "two_stage",
            "demo",
            "--workspace",
            "ws",
            "--source",
            "source.md",
            "--vault",
            "vault",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(invocation.settings.data_dir.as_deref(), Some("data"));
    assert_eq!(invocation.settings.backend, VectorBackend::Postgres);
    assert_eq!(
        invocation.settings.dsn.as_deref(),
        Some("postgres://example")
    );
    assert!(invocation.settings.split_derived_knowledge);
    assert_eq!(
        invocation.settings.conversation_persistence_mode,
        ConversationPersistenceMode::TwoStage
    );
    assert_eq!(invocation.command.as_str(), "demo");
    assert_eq!(
        invocation.arguments.get("workspace").map(String::as_str),
        Some("ws")
    );
}

#[test]
fn infers_defaults_from_environment() {
    let environment = BTreeMap::from([
        ("KOGWISTAR_VECTOR_BACKEND".to_string(), "qdrant".to_string()),
        (
            "KOGWISTAR_POSTGRES_DSN".to_string(),
            "postgres://env".to_string(),
        ),
    ]);
    let invocation = parse_cli_with_env(
        &arguments(&["report", "--workspace", "ws", "--data-dir", "data"]),
        &environment,
    )
    .unwrap();
    assert_eq!(invocation.settings.backend, VectorBackend::Qdrant);
    assert_eq!(invocation.settings.dsn.as_deref(), Some("postgres://env"));
    assert_eq!(invocation.settings.data_dir, None);
    assert_eq!(invocation.settings.backend.as_str(), "qdrant");
}

#[test]
fn rejects_invalid_choices() {
    let environment = BTreeMap::new();
    let backend_error =
        parse_cli_with_env(&arguments(&["--backend", "redis", "demo"]), &environment).unwrap_err();
    assert_eq!(
        backend_error,
        CliError::InvalidChoice("--backend".to_string(), "redis".to_string())
    );
    let persistence_error = parse_cli_with_env(
        &arguments(&["--conversation-persistence-mode", "three_stage", "demo"]),
        &environment,
    )
    .unwrap_err();
    assert_eq!(
        persistence_error,
        CliError::InvalidChoice(
            "--conversation-persistence-mode".to_string(),
            "three_stage".to_string()
        )
    );
}

#[test]
fn rejects_missing_or_unknown_command() {
    let environment = BTreeMap::new();
    assert_eq!(
        parse_cli_with_env(&[], &environment).unwrap_err(),
        CliError::MissingCommand
    );
    let unknown = parse_cli_with_env(&arguments(&["unknown"]), &environment).unwrap_err();
    assert_eq!(unknown, CliError::UnknownCommand("unknown".to_string()));
}
