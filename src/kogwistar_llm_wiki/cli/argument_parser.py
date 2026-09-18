from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping

CommandHandler = Callable[[argparse.Namespace], object]


def build_argument_parser(handlers: Mapping[str, CommandHandler]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kogwistar_llm_wiki",
        description="LLM-Wiki CLI",
    )
    parser.add_argument("--data-dir", default=None, help="Path to persistent data directory")
    parser.add_argument(
        "--backend",
        choices=["chroma", "postgres"],
        default="chroma",
        help="Backend to use under --data-dir (default: chroma)",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("KOGWISTAR_POSTGRES_DSN"),
        help="PostgreSQL DSN for --backend postgres (or KOGWISTAR_POSTGRES_DSN)",
    )
    parser.add_argument(
        "--split-derived-knowledge",
        action="store_true",
        help="Host derived knowledge on a dedicated engine instead of reusing raw KG",
    )
    parser.add_argument(
        "--conversation-persistence-mode",
        choices=["single_stage", "two_stage"],
        default="single_stage",
        help="Conversation graph materialization mode; two_stage defers semantic embeddings to the batch worker",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser(
        "demo",
        help="Run the one-process in-memory demo and write the vault before exiting",
    )
    demo_p.add_argument("--workspace", required=True, help="Workspace ID")
    demo_p.add_argument("--source", required=True, help="Path to the source document")
    demo_p.add_argument("--vault", required=True, help="Obsidian vault root path")
    demo_p.add_argument("--title", default=None, help="Optional document title")
    demo_p.add_argument(
        "--source-format",
        choices=["text", "markdown"],
        default="text",
        help="How to interpret the source document",
    )
    demo_p.add_argument(
        "--operation-mode",
        choices=["parse_first", "maintenance_first", "hybrid"],
        default="parse_first",
        help="Ingest operation mode",
    )
    demo_p.add_argument(
        "--parser-mode",
        choices=["heuristic", "ollama", "gemini", "openai", "azure_openai"],
        default="heuristic",
        help="Parser mode to use for the document",
    )
    demo_p.add_argument(
        "--parser-lane",
        choices=["page_index", "workflow_layered"],
        default="page_index",
        help="Parser lane to use for the document",
    )
    demo_p.add_argument(
        "--llm-provider",
        default=None,
        help="Explicit parser provider override (for example ollama, gemini, openai, or azure_openai)",
    )
    demo_p.add_argument(
        "--llm-model",
        default=None,
        help="Explicit parser model or deployment name override",
    )
    demo_p.add_argument(
        "--promotion-mode",
        choices=["pending", "sync"],
        default="sync",
        help="Whether to auto-promote the extracted knowledge",
    )
    demo_p.add_argument(
        "--debug-run-dir",
        default=os.environ.get("KOGWISTAR_DEBUG_RUN_DIR"),
        help="Write debug traces, logs, and sqlite statistics to this directory",
    )
    demo_p.set_defaults(func=handlers["demo"])

    ingest_p = sub.add_parser("ingest", help="Read a source document into the workspace")
    ingest_p.add_argument("--workspace", required=True, help="Workspace ID")
    ingest_p.add_argument("--source", required=True, help="Path to the source document")
    ingest_p.add_argument("--title", default=None, help="Optional document title")
    ingest_p.add_argument(
        "--source-format",
        choices=["text", "markdown"],
        default="text",
        help="How to interpret the source document",
    )
    ingest_p.add_argument(
        "--operation-mode",
        choices=["parse_first", "maintenance_first", "hybrid"],
        default="parse_first",
        help="Ingest operation mode",
    )
    ingest_p.add_argument(
        "--parser-mode",
        choices=["heuristic", "ollama", "gemini", "openai", "azure_openai"],
        default="heuristic",
        help="Parser mode to use for the document",
    )
    ingest_p.add_argument(
        "--parser-lane",
        choices=["page_index", "workflow_layered"],
        default="page_index",
        help="Parser lane to use for the document",
    )
    ingest_p.add_argument(
        "--llm-provider",
        default=None,
        help="Explicit parser provider override (for example ollama, gemini, openai, or azure_openai)",
    )
    ingest_p.add_argument(
        "--llm-model",
        default=None,
        help="Explicit parser model or deployment name override",
    )
    ingest_p.add_argument(
        "--promotion-mode",
        choices=["pending", "sync"],
        default="sync",
        help="Whether to auto-promote the extracted knowledge",
    )
    ingest_p.add_argument(
        "--debug-run-dir",
        default=os.environ.get("KOGWISTAR_DEBUG_RUN_DIR"),
        help="Write debug traces, logs, and sqlite statistics to this directory",
    )
    ingest_p.set_defaults(func=handlers["ingest"])

    report_p = sub.add_parser(
        "report",
        help="Inspect the persisted wiki graph and summarize quality signals",
    )
    report_p.add_argument("--workspace", required=True, help="Workspace ID")
    report_p.add_argument("--data-dir", required=True, help="Path to persistent data directory")
    report_p.add_argument(
        "--backend",
        choices=["chroma", "postgres"],
        default="chroma",
        help="Backend to use under --data-dir (default: chroma)",
    )
    report_p.add_argument(
        "--dsn",
        default=None,
        help="PostgreSQL DSN for --backend postgres",
    )
    report_p.add_argument(
        "--report-scope",
        choices=["llm_wiki", "maintenance", "thinking", "all"],
        default="all",
        help="Select which graph lanes to include in the report dump.",
    )
    report_p.add_argument(
        "--dump-mode",
        choices=["summary", "raw", "both"],
        default="summary",
        help="Choose whether to emit only the summary sink, raw payloads, or both.",
    )
    report_p.add_argument(
        "--dump-limit-per-space",
        type=int,
        default=20,
        help="Maximum raw artifacts to include per graph space when dump-mode is raw or both.",
    )
    report_p.add_argument(
        "--split-derived-knowledge",
        action="store_true",
        help="Host derived knowledge on a dedicated engine instead of reusing raw KG",
    )
    report_p.set_defaults(func=handlers["report"])

    workbench_p = sub.add_parser(
        "workbench",
        help="Serve the interactive graph workbench with durable Codex workers",
    )
    workbench_p.add_argument("--workspace", required=True, help="Workspace ID")
    workbench_p.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    workbench_p.add_argument("--port", type=int, default=8765, help="HTTP bind port")
    workbench_p.add_argument("--codex-workers", type=int, default=1, help="Concurrent Codex turns")
    workbench_p.add_argument("--codex-executable", default=None, help="Codex executable override")
    workbench_p.add_argument("--codex-model", default=None, help="Codex model override")
    workbench_p.add_argument("--codex-profile", default=None, help="Codex CLI profile")
    workbench_p.add_argument(
        "--codex-transport",
        choices=["exec", "app_server"],
        default=None,
        help="Codex transport (default: KOGWISTAR_CODEX_TRANSPORT or exec)",
    )
    workbench_p.add_argument("--codex-timeout", type=int, default=300, help="Per-turn timeout in seconds")
    workbench_p.set_defaults(func=handlers["workbench"])

    mcp_p = sub.add_parser(
        "mcp",
        help="Serve the full llm-wiki MCP tool server (requires the agent extra)",
    )
    mcp_p.add_argument("--workspace", required=True, help="Workspace ID")
    mcp_p.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    mcp_p.add_argument("--port", type=int, default=8780, help="HTTP MCP port")
    mcp_p.add_argument("--path", default="/mcp", help="HTTP MCP path")
    mcp_p.add_argument("--transport", choices=["stdio", "http", "streamable-http"], default="stdio")
    mcp_p.add_argument("--split-derived-knowledge", action="store_true")
    mcp_p.set_defaults(func=handlers["mcp"])

    serve_p = sub.add_parser(
        "serve",
        help="Run REST, MCP, and maintenance in one shared process",
    )
    serve_p.add_argument("--workspace", required=True, help="Workspace ID")
    serve_p.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    serve_p.add_argument("--port", type=int, default=8765, help="REST workbench port")
    serve_p.add_argument("--mcp-port", type=int, default=8780, help="MCP port")
    serve_p.add_argument("--mcp-path", default="/mcp", help="MCP HTTP path")
    serve_p.add_argument("--maintenance-interval", type=float, default=10.0)
    serve_p.add_argument("--background-interval", type=float, default=600.0)
    serve_p.set_defaults(func=handlers["serve"])

    embedding_p = sub.add_parser(
        "embedding-service",
        aliases=["embedding-service"],
        help="Serve one isolated Qwen3-VL embedding profile",
    )
    embedding_p.set_defaults(func=handlers["embedding_service"])

    seed_p = sub.add_parser(
        "seed-bundle",
        help="Seed a grounded graph bundle, optionally review it in Codex cockpit mode, and export it",
    )
    seed_p.add_argument("--workspace", required=True, help="Workspace ID")
    seed_p.add_argument("--bundle", required=True, help="Input seed-bundle JSON path")
    seed_p.add_argument("--output", required=True, help="Canonical JSON export path")
    seed_p.add_argument(
        "--cockpit-question",
        default=None,
        help="Optional real Codex cockpit question used to verify the persisted graph",
    )
    seed_p.add_argument("--cockpit-session", default="seed-review", help="Cockpit history session ID")
    seed_p.add_argument(
        "--cockpit-interaction",
        default="seed-review-v1",
        help="Stable idempotency ID for the cockpit interaction",
    )
    seed_p.add_argument("--codex-executable", default=None, help="Codex executable override")
    seed_p.add_argument("--codex-model", default=None, help="Codex model override")
    seed_p.add_argument("--codex-profile", default=None, help="Codex CLI profile")
    seed_p.add_argument(
        "--codex-transport",
        choices=["exec", "app_server"],
        default=None,
        help="Codex transport (default: KOGWISTAR_CODEX_TRANSPORT or exec)",
    )
    seed_p.add_argument("--codex-timeout", type=int, default=300, help="Per-action timeout in seconds")
    seed_p.set_defaults(func=handlers["seed_bundle"])

    archive_p = sub.add_parser(
        "archive",
        help="Create or inspect operator-only portable event archives",
    )
    archive_sub = archive_p.add_subparsers(dest="archive_command", required=True)

    archive_create_p = archive_sub.add_parser("create", help="Create a quiescent base or incremental archive")
    archive_create_p.add_argument("--workspace", required=True, help="Workspace ID")
    archive_create_p.add_argument("--output", required=True, help="Output .tar.gz archive path")
    archive_create_p.add_argument("--parent", default=None, help="Verified parent archive for an incremental archive")
    archive_create_p.add_argument("--include-backend-snapshot", action="store_true", help="Include local persistent backend directories")
    archive_create_p.set_defaults(func=handlers["archive_create"])

    for name, handler in (("inspect", handlers["archive_inspect"]), ("verify", handlers["archive_verify"])):
        command_p = archive_sub.add_parser(name, help=f"{name.title()} an archive without changing state")
        command_p.add_argument("--archive", required=True, help="Archive .tar.gz path")
        command_p.set_defaults(func=handler)

    archive_restore_p = archive_sub.add_parser("restore", help="Validate or restore to a fresh target datastore")
    archive_restore_p.add_argument("--archive", required=True, help="Base or incremental archive path")
    archive_restore_p.add_argument("--parent", action="append", default=[], help="Parent archive path, oldest first")
    archive_restore_p.add_argument("--target-workspace", default=None, help="Target workspace ID; required with --apply")
    archive_restore_p.add_argument("--apply", action="store_true", help="Apply after validation; default is dry-run")
    archive_restore_p.add_argument("--use-backend-snapshot", action="store_true", help="Restore an exact compatible fast backend snapshot")
    archive_restore_p.add_argument("--embedding-fingerprint", default=None, help="Exact embedding fingerprint required for fast snapshot restore")
    archive_restore_p.set_defaults(func=handlers["archive_restore"])

    archive_catalog_p = archive_sub.add_parser("catalog", help="List verified archives in a directory")
    archive_catalog_p.add_argument("--directory", required=True, help="Archive directory")
    archive_catalog_p.add_argument("--before-ms", type=int, default=None, help="Only show archives captured by this epoch-millisecond")
    archive_catalog_p.set_defaults(func=handlers["archive_catalog"])

    embeddings_p = sub.add_parser(
        "embeddings",
        help="Inspect or explicitly adopt persistent embedding profiles",
    )
    embeddings_sub = embeddings_p.add_subparsers(dest="embedding_command", required=True)
    embedding_inspect_p = embeddings_sub.add_parser(
        "inspect", help="Report configured, registered, and physical embedding state"
    )
    embedding_inspect_p.add_argument("--workspace", required=True, help="Workspace ID")
    embedding_inspect_p.set_defaults(func=handlers["embeddings_inspect"])
    embedding_adopt_p = embeddings_sub.add_parser(
        "adopt-legacy-profile",
        help="Bind the configured profile to already-populated unregistered storage",
    )
    embedding_adopt_p.add_argument("--workspace", required=True, help="Workspace ID")
    embedding_adopt_p.add_argument(
        "--acknowledge-legacy-vectors",
        action="store_true",
        help="Acknowledge that existing vectors were verified against the configured profile",
    )
    embedding_adopt_p.set_defaults(func=handlers["embeddings_adopt_legacy"])

    codex_memory_p = sub.add_parser(
        "codex-memory",
        help="Validate a project binding and print Codex MCP memory instructions",
    )
    codex_memory_p.add_argument("--workspace", required=True, help="Isolated LLM-Wiki workspace ID")
    codex_memory_p.add_argument(
        "--project-root",
        default=".",
        help="Project root used for the stable local binding key (default: current directory)",
    )
    codex_memory_p.add_argument(
        "--mcp-url",
        default="http://127.0.0.1:8780",
        help="Base URL of the LLM-Wiki MCP server",
    )
    codex_memory_p.add_argument(
        "--binding-file",
        default=None,
        help="Optional app-owned JSON binding file; defaults below --data-dir/settings",
    )
    codex_memory_p.add_argument(
        "--check-only",
        action="store_true",
        help="Validate without creating a missing project binding",
    )
    codex_memory_p.set_defaults(func=handlers["codex_memory"])

    bridge_p = sub.add_parser(
        "codex-bridge",
        help="Serve a bounded, user-scoped Codex bridge for Docker maintenance",
    )
    bridge_p.add_argument("--host", default="0.0.0.0", help="Bind address; use 0.0.0.0 for Docker Desktop reachability")
    bridge_p.add_argument("--port", type=int, default=8791, help="Bridge port")
    bridge_p.add_argument("--token-env", default="LLM_WIKI_CODEX_BRIDGE_TOKEN", help="Environment variable containing the bridge token")
    bridge_p.add_argument("--env-file", default=".env", help="Host dotenv file to load for bridge settings (default: .env)")
    bridge_p.add_argument("--no-env-file", action="store_true", help="Do not load the host dotenv file")
    bridge_p.add_argument("--codex-executable", default=None, help="Optional Codex executable override")
    bridge_p.add_argument("--codex-model", default=None, help="Optional Codex model override")
    bridge_p.set_defaults(func=handlers["codex_bridge"])

    codex_compose_p = sub.add_parser(
        "codex-compose",
        help="Interactive TUI for starting standalone Codex or the memory stack",
    )
    codex_compose_p.add_argument("--mode", choices=["standalone", "memory", "host", "host-memory"])
    codex_compose_p.add_argument("--project", default="llm-wiki-memory")
    codex_compose_p.add_argument("--configure", action="store_true")
    codex_compose_p.add_argument("--stack", choices=["split", "combined"], default="split")
    codex_compose_p.add_argument("--embedding", choices=["none", "vllm", "transformers"], default="none")
    codex_compose_p.add_argument("--maintenance-enabled", choices=["true", "false"], default="false", dest="maintenance_enabled")
    codex_compose_p.add_argument("--request-enabled", choices=["true", "false"], default="true", dest="request_enabled")
    codex_compose_p.add_argument("--background-enabled", choices=["true", "false"], default="false", dest="background_enabled")
    codex_compose_p.add_argument("--maintenance-profile", choices=["high", "balanced", "budgeted", "lite"], default="balanced")
    codex_compose_p.add_argument("--profile-ladder", dest="profile_ladder_json", default=None)
    codex_compose_p.add_argument("--profile-ladder-file", default=None)
    codex_compose_p.add_argument("--combined-memory", default="512m")
    codex_compose_p.add_argument("--combined-cpu", default="0.25")
    codex_compose_p.add_argument("--embedding-dimension", type=int, default=1024)
    codex_compose_p.add_argument("--max-model-len", type=int, default=8192, dest="max_model_len")
    codex_compose_p.add_argument("--crop-token-budget", type=int, default=7680, dest="crop_token_budget")
    codex_compose_p.add_argument("--env-file", default=".env", dest="env_file")
    codex_compose_p.add_argument("--write-env", action="store_true", dest="write_env")
    codex_compose_p.add_argument("--lxc", action="store_true")
    codex_compose_p.add_argument("--lxc-apply", action="store_true", dest="lxc_apply")
    codex_compose_p.add_argument("--build", action="store_true")
    codex_compose_p.add_argument("--login", action="store_true")
    codex_compose_p.add_argument("--dry-run", action="store_true")
    codex_compose_p.add_argument("--execute", action="store_true")
    codex_compose_p.set_defaults(func=lambda args: handlers["codex_compose"](args))

    compose_p = sub.add_parser("compose", help="Generate or validate a safe Docker Compose bundle")
    compose_sub = compose_p.add_subparsers(dest="compose_command", required=True)
    compose_generate_p = compose_sub.add_parser("generate", help="Generate a self-contained Compose configuration")
    compose_generate_p.add_argument("--output", required=True, help="Output YAML path")
    compose_generate_p.add_argument("--workspace", default="default")
    compose_generate_p.add_argument("--backend", choices=["postgres", "chroma"], default="postgres")
    compose_generate_p.add_argument("--project-name", default="llm-wiki")
    compose_generate_p.add_argument("--mode", choices=["gpu", "cpu", "text-only"], default="gpu")
    compose_generate_p.add_argument(
        "--embedding-backend",
        choices=["auto", "vllm", "transformers"],
        default="auto",
        help="Multimodal backend; auto selects vLLM for GPU and Transformers for CPU",
    )
    compose_generate_p.add_argument("--auth-mode", choices=["disabled", "static_token", "kogwistar_jwt"], default="disabled")
    compose_generate_p.add_argument("--with-otel", action="store_true")
    compose_generate_p.add_argument("--with-oauth", action="store_true")
    compose_generate_p.add_argument("--model-revision", default="")
    compose_generate_p.add_argument("--embedding-dimension", type=int, default=1024)
    compose_generate_p.set_defaults(func=handlers["compose_generate"])
    compose_check_p = compose_sub.add_parser("check", help="Validate a generated or checked-in Compose YAML")
    compose_check_p.add_argument("--file", required=True, help="Compose YAML path")
    compose_check_p.set_defaults(func=handlers["compose_check"])

    # daemon sub-command
    daemon_p = sub.add_parser("daemon", help="Run a background daemon")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_type", required=True)

    proj_p = daemon_sub.add_parser("projection", help="Obsidian projection daemon")
    proj_p.add_argument("--workspace", required=True, help="Workspace ID")
    proj_p.add_argument("--vault", required=True, help="Obsidian vault root path")
    proj_p.add_argument("--interval", type=float, default=5.0, help="Poll interval (seconds)")
    proj_p.set_defaults(func=handlers["daemon_projection"])

    maint_p = daemon_sub.add_parser("maintenance", help="Maintenance distillation daemon")
    maint_p.add_argument("--workspace", required=True, help="Workspace ID")
    maint_p.add_argument("--interval", type=float, default=10.0, help="Poll interval (seconds)")
    maint_p.add_argument("--background-interval", type=float, default=600.0, help="Background cycle interval (seconds)")
    maint_p.set_defaults(func=handlers["daemon_maintenance"])

    control_p = daemon_sub.add_parser("maintenance-control", help="Change maintenance modes through the local daemon control channel")
    control_p.add_argument("--request-enabled", choices=["true", "false"], default=None)
    control_p.add_argument("--background-enabled", choices=["true", "false"], default=None)
    control_p.add_argument("--enabled", choices=["true", "false"], default=None, help="Enable or pause profile-driven maintenance")
    control_p.add_argument("--profile", choices=["high", "balanced", "budgeted", "lite"], default=None)
    control_p.add_argument("--profile-ladder-json", default=None, help="Ordered JSON profile/provider quota ladder")
    control_p.add_argument("--budget-json", default=None, help="Windowed budget JSON, for example {\"daily\":{\"output_tokens\":20000}}")
    control_p.add_argument("--clear-budget", action="store_true")
    control_p.add_argument("--status", action="store_true", help="Print durable control state without changing it")
    for window in ("daily", "weekly", "monthly"):
        for metric in ("tokens", "input_tokens", "output_tokens", "money"):
            control_p.add_argument(f"--{window.replace('_', '-')}-{metric.replace('_', '-')}", dest=f"{window}_{metric}", type=float, default=None)
    control_p.add_argument("--runtime-only", action="store_true", help="Do not persist the requested state")
    control_p.set_defaults(func=handlers["maintenance_control"])
    return parser

