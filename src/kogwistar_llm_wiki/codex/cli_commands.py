"""CLI handlers for Codex memory, bridge, compose, and seed workflows."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path

from ..providers.model_catalog import _safe_endpoint

logger = logging.getLogger(__name__)

EngineBuilder = Callable[..., object]
EngineCloser = Callable[[object], None]
PersistenceOptions = Callable[[argparse.Namespace], Mapping[str, str]]
EnvLoader = Callable[[Path], None]


def codex_memory(
    args: argparse.Namespace,
    *,
    build_engines: EngineBuilder,
    close_engines: EngineCloser,
) -> None:
    """Validate a project memory binding and print safe Codex MCP settings."""
    from ..ingest_pipeline import IngestPipeline
    from ..workbench.workbench_api import WorkbenchApi

    engines = build_engines(args.workspace, args.data_dir, args.backend, args.dsn)
    try:
        pipeline = IngestPipeline(engines)
        api = WorkbenchApi(pipeline)
        project_root = Path(args.project_root).expanduser().resolve()
        project_key = sha256(str(project_root).encode("utf-8")).hexdigest()[:24]
        binding_path = Path(args.binding_file).expanduser() if args.binding_file else None
        if binding_path is None:
            data_dir = args.data_dir or os.environ.get("KOGWISTAR_DATA_DIR")
            if data_dir:
                binding_path = Path(data_dir) / "settings" / "codex-memory-bindings.json"
        binding: dict[str, object] = {}
        if binding_path is not None and binding_path.exists():
            try:
                loaded = json.loads(binding_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot read Codex memory binding file: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ValueError("Codex memory binding file must contain an object")
            binding = loaded
        bindings = binding.get("bindings", {})
        if not isinstance(bindings, dict):
            raise TypeError("Codex memory binding file bindings must be an object")
        existing = bindings.get(project_key)
        if isinstance(existing, dict) and str(existing.get("workspace_id") or "") != args.workspace:
            raise ValueError(
                f"project is already bound to workspace {existing.get('workspace_id')!r}; "
                "use a new project root or change the binding explicitly"
            )
        if existing is None and binding_path is not None and not args.check_only:
            binding_path.parent.mkdir(parents=True, exist_ok=True)
            bindings[project_key] = {
                "workspace_id": args.workspace,
                "project_key": project_key,
                "created_at_ms": int(time.time() * 1000),
            }
            binding = {"version": 1, "bindings": bindings}
            binding_path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        mcp_url = _safe_endpoint(args.mcp_url).rstrip("/")
        if not mcp_url:
            raise ValueError("--mcp-url must be a credential-free http(s) URL")
        token_configured = bool(
            os.getenv("LLM_WIKI_MCP_TOKEN", "").strip()
            or os.getenv("LLM_WIKI_API_TOKEN", "").strip()
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "workspace_id": args.workspace,
                    "project_key": project_key,
                    "binding_file": str(binding_path) if binding_path is not None else None,
                    "binding_written": existing is None and binding_path is not None and not args.check_only,
                    "readiness": api.readiness(),
                    "memory": {
                        "enabled": api.codex_memory.enabled,
                        "max_records_per_capture": api.codex_memory.max_records_per_capture,
                        "max_recall_records": api.codex_memory.max_recall_records,
                    },
                    "mcp": {
                        "server_name": "llm-wiki",
                        "url": f"{mcp_url}/mcp",
                        "authorization_required": token_configured,
                        "authorization": "Bearer <LLM_WIKI_MCP_TOKEN>" if token_configured else None,
                    },
                    "instructions": [
                        "Call memory_recall before relevant project planning, debugging, design, or continuation work.",
                        "Capture only structured project facts with bounded evidence; label inferred records.",
                        "Use memory_review when current evidence conflicts with prior memory.",
                        "Use propose and confirm for canonical graph changes.",
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        close_engines(engines)


def codex_compose(args: argparse.Namespace) -> None:
    """Run the guided Codex Compose TUI."""
    from .codex_compose_tui import main as run_tui

    tui_args: list[str] = []
    tui_option_names = {"profile_ladder_json": "profile-ladder"}
    for name in (
        "mode",
        "project",
        "stack",
        "embedding",
        "maintenance_enabled",
        "request_enabled",
        "background_enabled",
        "maintenance_profile",
        "profile_ladder_json",
        "profile_ladder_file",
        "combined_memory",
        "combined_cpu",
        "embedding_dimension",
        "max_model_len",
        "crop_token_budget",
        "env_file",
    ):
        value = getattr(args, name, None)
        if value:
            option_name = tui_option_names.get(name, name.replace("_", "-"))
            tui_args.extend([f"--{option_name}", str(value)])
    for name in ("build", "login", "dry_run", "execute", "configure", "write_env", "lxc", "lxc_apply"):
        if getattr(args, name, False):
            tui_args.append(f"--{name.replace('_', '-')}")
    exit_code = run_tui(tui_args)
    if exit_code:
        raise SystemExit(exit_code)


def seed_bundle(
    args: argparse.Namespace,
    *,
    build_engines: EngineBuilder,
    close_engines: EngineCloser,
    persistence_kwargs: PersistenceOptions,
) -> None:
    """Seed, optionally inspect through cockpit mode, and export a graph bundle."""
    from ..ingest_pipeline import IngestPipeline
    from ..seeding.operations import (
        dump_seed_bundle,
        export_graph_seed_bundle,
        load_seed_bundle,
        seed_graph_bundle,
    )
    from ..workbench.workbench_api import WorkbenchApi
    from .codex_workbench_agent import CodexCliCockpitResponder, CodexCliSettings

    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
    )
    api: WorkbenchApi | None = None
    try:
        bundle = load_seed_bundle(args.bundle)
        seeded = seed_graph_bundle(engines, workspace_id=args.workspace, bundle=bundle)
        cockpit_response: dict[str, object] | None = None
        if args.cockpit_question:
            responder = CodexCliCockpitResponder(
                CodexCliSettings(
                    executable=args.codex_executable,
                    model=args.codex_model,
                    profile=args.codex_profile,
                    timeout_seconds=args.codex_timeout,
                    transport=getattr(args, "codex_transport", None)
                    or os.environ.get("KOGWISTAR_CODEX_TRANSPORT", "exec"),
                ),
                trace_line=lambda line: logger.info("seed_cockpit_trace %s", line),
            )
            api = WorkbenchApi(
                IngestPipeline(engines, **persistence_kwargs(args)),
                cockpit_responder=responder,
            )
            cockpit_response = api.ask(
                {
                    "workspace_id": args.workspace,
                    "session_id": args.cockpit_session,
                    "interaction_id": args.cockpit_interaction,
                    "mode": "codex",
                    "query_text": args.cockpit_question,
                    "graph_spaces": ["curated_kg"],
                    "semantic_retrieval": False,
                    "max_nodes": 48,
                    "max_edges": 48,
                    "max_hyperedges": 12,
                    "hop_limit": 2,
                }
            )
        exported = export_graph_seed_bundle(
            engines,
            workspace_id=args.workspace,
            bundle_id=bundle.bundle_id,
        )
        output_path = dump_seed_bundle(exported, args.output)
        cockpit_summary: dict[str, object] | None = None
        if cockpit_response is not None:
            cockpit_path = output_path.with_name(output_path.stem + ".cockpit.json")
            cockpit_path.write_text(json.dumps(cockpit_response, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            answer = dict(cockpit_response.get("answer") or {})
            cockpit_summary = {
                "agent_status": cockpit_response.get("agent_status"),
                "interaction_id": cockpit_response.get("interaction_id"),
                "outcome": answer.get("outcome"),
                "answer": answer.get("text"),
                "cited_entity_ids": answer.get("cited_entity_ids"),
                "artifact_path": str(cockpit_path.resolve()),
            }
        result = {
            "workspace_id": args.workspace,
            "bundle_id": bundle.bundle_id,
            "seed": asdict(seeded),
            "export_path": str(output_path.resolve()),
            "integrity": {
                "round_trip_equal": exported == bundle,
                "sources": len(exported.sources),
                "nodes": len(exported.nodes),
                "edges": len(exported.edges),
                "hyperedges": len(exported.hyperedges),
            },
            "cockpit": cockpit_summary,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        if exported != bundle:
            raise RuntimeError("persisted seed bundle failed canonical round-trip integrity")
    finally:
        if api is not None:
            api.close()
        close_engines(engines)


def codex_bridge(args: argparse.Namespace, *, load_env_file: EnvLoader) -> None:
    """Run the user-scoped bridge used by Docker maintenance workers."""
    from .codex_bridge import bridge_settings_from_environment, serve_codex_bridge

    if not args.no_env_file:
        load_env_file(Path(args.env_file).expanduser())
    token = os.environ.get(args.token_env, "")
    if not token:
        raise SystemExit(f"set {args.token_env} before starting the Codex bridge")
    settings = bridge_settings_from_environment()
    if args.codex_executable:
        settings = replace(settings, executable=args.codex_executable)
    if args.codex_model:
        settings = replace(settings, model=args.codex_model)
    serve_codex_bridge(host=args.host, port=args.port, token=token, settings=settings)
