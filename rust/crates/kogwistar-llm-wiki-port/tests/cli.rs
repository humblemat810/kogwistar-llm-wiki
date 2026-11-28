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
    assert_eq!(invocation.first_argument("workspace"), Some("ws"));
    assert_eq!(invocation.first_argument("source"), Some("a.md"));
    assert_eq!(invocation.first_argument("vault"), Some("vault"));
    assert_eq!(invocation.first_argument("title"), Some("Title"));
    assert_eq!(invocation.first_argument("source_format"), Some("markdown"));
    assert_eq!(invocation.first_argument("parser_mode"), Some("heuristic"));
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
        invocation.first_argument("split_derived_knowledge"),
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
    assert_eq!(invocation.first_argument("workspace"), Some("ws"));
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

#[test]
fn routes_nested_commands_to_python_handler_names() {
    let environment = BTreeMap::new();
    let archive_create = parse_cli_with_env(
        &arguments(&[
            "archive",
            "create",
            "--workspace",
            "ws",
            "--output",
            "out.tar.gz",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(archive_create.subcommand.as_deref(), Some("create"));
    assert_eq!(archive_create.resolved_handler_name(), "archive_create");

    let archive_restore = parse_cli_with_env(
        &arguments(&[
            "archive",
            "restore",
            "--archive",
            "base.tar.gz",
            "--parent",
            "old.tar.gz",
            "--parent",
            "older.tar.gz",
            "--apply",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(archive_restore.resolved_handler_name(), "archive_restore");
    assert_eq!(
        archive_restore.argument_values("parent").unwrap(),
        &["old.tar.gz".to_string(), "older.tar.gz".to_string()]
    );

    let embeddings_adopt = parse_cli_with_env(
        &arguments(&[
            "embeddings",
            "adopt-legacy-profile",
            "--workspace",
            "ws",
            "--acknowledge-legacy-vectors",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(
        embeddings_adopt.resolved_handler_name(),
        "embeddings_adopt_legacy"
    );
    assert_eq!(
        embeddings_adopt.first_argument("acknowledge_legacy_vectors"),
        Some("true")
    );

    let compose_generate = parse_cli_with_env(
        &arguments(&[
            "compose",
            "generate",
            "--output",
            "compose.yml",
            "--with-otel",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(compose_generate.resolved_handler_name(), "compose_generate");
    assert_eq!(compose_generate.first_argument("with_otel"), Some("true"));
}

#[test]
fn rejects_missing_nested_subcommands() {
    let environment = BTreeMap::new();
    for command in ["archive", "embeddings", "compose", "daemon"] {
        let error = parse_cli_with_env(&arguments(&[command]), &environment).unwrap_err();
        assert_eq!(error, CliError::MissingSubcommand(command.to_string()));
    }
}

#[test]
fn routes_daemon_lifecycle_commands_and_payloads() {
    let environment = BTreeMap::new();
    let projection = parse_cli_with_env(
        &arguments(&[
            "daemon",
            "projection",
            "--workspace",
            "ws",
            "--vault",
            "vault",
            "--interval",
            "5",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(projection.resolved_handler_name(), "daemon_projection");
    assert_eq!(projection.first_argument("workspace"), Some("ws"));
    assert_eq!(projection.first_argument("vault"), Some("vault"));
    assert_eq!(projection.first_argument("interval"), Some("5"));

    let maintenance = parse_cli_with_env(
        &arguments(&[
            "daemon",
            "maintenance",
            "--workspace",
            "ws",
            "--interval",
            "10",
            "--background-interval",
            "600",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(maintenance.resolved_handler_name(), "daemon_maintenance");
    assert_eq!(
        maintenance.first_argument("background_interval"),
        Some("600")
    );

    let control = parse_cli_with_env(
        &arguments(&[
            "daemon",
            "maintenance-control",
            "--request-enabled",
            "true",
            "--background-enabled",
            "false",
            "--profile",
            "balanced",
            "--daily-output-tokens",
            "20000",
            "--status",
        ]),
        &environment,
    )
    .unwrap();
    assert_eq!(control.resolved_handler_name(), "maintenance_control");
    assert_eq!(control.first_argument("request_enabled"), Some("true"));
    assert_eq!(control.first_argument("background_enabled"), Some("false"));
    assert_eq!(control.first_argument("profile"), Some("balanced"));
    assert_eq!(control.first_argument("daily_output_tokens"), Some("20000"));
    assert_eq!(control.first_argument("status"), Some("true"));
}

#[test]
fn rejects_missing_daemon_required_arguments() {
    let environment = BTreeMap::new();
    let projection = parse_cli_with_env(
        &arguments(&["daemon", "projection", "--workspace", "ws"]),
        &environment,
    )
    .unwrap_err();
    assert_eq!(projection, CliError::MissingArgument("vault".to_string()));
    let maintenance = parse_cli_with_env(
        &arguments(&["daemon", "maintenance", "--interval", "10"]),
        &environment,
    )
    .unwrap_err();
    assert_eq!(
        maintenance,
        CliError::MissingArgument("workspace".to_string())
    );
}
