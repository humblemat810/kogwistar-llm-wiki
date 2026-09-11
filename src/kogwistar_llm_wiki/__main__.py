"""CLI entry point: ``python -m kogwistar_llm_wiki``

Sub-commands
------------
demo --workspace <id> --source <path> --vault <path> [--title <text>]
    Run the ephemeral end-to-end demo in one process. Uses the in-memory
    engine bundle, writes the same source and base-knowledge graph spaces as
    normal ingest, then renders the Obsidian vault from explicit graph-space
    reads before exiting.

ingest --workspace <id> --source <path> [--title <text>] [--promotion-mode <mode>]
    Read a source document and populate the workspace state.

report --workspace <id> --data-dir <path> [--backend <name>] [--dsn <dsn>]
    Inspect the persisted wiki graph, summarize quality signals, and emit a
    small node/edge sample sink for review.
    Use --report-scope to choose llm-wiki, maintenance, thinking, or all.
    Use --dump-mode raw or both when you want raw Kogwistar payloads too.

daemon projection --workspace <id> --vault <path> [--interval <s>]
    Run the Obsidian projection daemon (blocking).

daemon maintenance --workspace <id> [--interval <s>]
    Run the maintenance distillation daemon (blocking).

``demo`` is intentionally single-process and ephemeral so it does not depend on
any process-shared local backend.

The persistent commands expect ``--data-dir`` or ``KOGWISTAR_DATA_DIR`` to
point at a directory containing the local backend state. ``--data-dir`` wins
when both are provided. Use ``--backend postgres`` and ``--dsn`` to switch to a
PostgreSQL-backed store.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from .compose_config import ComposeOptions, check_compose_text, write_compose

if TYPE_CHECKING:
    from kogwistar_llm_wiki.models import IngestPipelineRequest, NamespaceEngines

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("kogwistar_llm_wiki")


def _conversation_persistence_kwargs(args: argparse.Namespace) -> dict[str, str]:
    """Forward the new engine option only when an operator opts in.

    This keeps existing command-handler seams and third-party builder wrappers
    compatible with the historical default single-stage invocation.
    """
    mode = str(getattr(args, "conversation_persistence_mode", "single_stage"))
    return {} if mode == "single_stage" else {"conversation_persistence_mode": mode}


def _close_engines(engines: "NamespaceEngines") -> None:
    """Close real engine bundles while remaining compatible with test doubles."""
    close = getattr(engines, "close", None)
    if callable(close):
        close()


def _build_engines(
    workspace_id: str,
    data_dir: str | None,
    backend: str,
    dsn: str | None,
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: str = "single_stage",
    embedding_profile_mode: str = "enforce",
) -> "NamespaceEngines":
    """Construct a NamespaceEngines bundle from the selected backend."""
    from kogwistar_llm_wiki.ingest_pipeline import (
        build_persistent_namespace_engines,
        build_postgres_namespace_engines,
    )

    effective_data_dir = data_dir or os.environ.get("KOGWISTAR_DATA_DIR")
    if not effective_data_dir:
        raise ValueError(
            "persistent commands require --data-dir or KOGWISTAR_DATA_DIR"
        )
    builder_kwargs: dict[str, object] = {
        "split_derived_knowledge": split_derived_knowledge,
    }
    if embedding_profile_mode != "enforce":
        builder_kwargs["embedding_profile_mode"] = embedding_profile_mode
    if conversation_persistence_mode != "single_stage":
        builder_kwargs["conversation_persistence_mode"] = conversation_persistence_mode
    if backend == "chroma":
        return build_persistent_namespace_engines(
            base_dir=effective_data_dir,
            **builder_kwargs,
        )
    if backend == "postgres":
        if not dsn:
            raise ValueError("--dsn is required when --backend postgres is selected")
        return build_postgres_namespace_engines(
            base_dir=effective_data_dir,
            dsn=dsn,
            **builder_kwargs,
        )
    raise ValueError(f"Unsupported backend: {backend!r}")


def _build_demo_engines(
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: str = "single_stage",
) -> "NamespaceEngines":
    from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines

    builder_kwargs: dict[str, object] = {
        "split_derived_knowledge": split_derived_knowledge,
    }
    if conversation_persistence_mode != "single_stage":
        builder_kwargs["conversation_persistence_mode"] = conversation_persistence_mode
    return build_in_memory_namespace_engines(**builder_kwargs)


def _read_request_from_source(args: argparse.Namespace) -> tuple[Path, "IngestPipelineRequest"]:
    from kogwistar_llm_wiki.models import IngestPipelineRequest

    source_path = Path(args.source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"source file not found: {source_path}")

    raw_text = source_path.read_text(encoding="utf-8")
    title = args.title or source_path.stem
    request = IngestPipelineRequest(
        workspace_id=args.workspace,
        source_uri=source_path.as_uri(),
        title=title,
        raw_text=raw_text,
        source_format=args.source_format,
        operation_mode=getattr(args, "operation_mode", "parse_first"),
        parser_mode=args.parser_mode,
        parser_lane=args.parser_lane,
        promotion_mode=args.promotion_mode,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
    )
    return source_path, request


def _read_demo_requests_from_source(args: argparse.Namespace) -> list[tuple[Path, "IngestPipelineRequest"]]:
    from kogwistar_llm_wiki.models import IngestPipelineRequest

    source_path = Path(args.source).expanduser().resolve()
    if source_path.is_file():
        _, request = _read_request_from_source(args)
        return [(source_path, request)]

    if not source_path.is_dir():
        raise FileNotFoundError(f"source path not found: {source_path}")

    corpus_paths = sorted(
        path
        for path in source_path.glob("*.md")
        if path.is_file() and path.name != "index.md" and path.name != "manifest.md"
    )
    if not corpus_paths:
        raise FileNotFoundError(f"no markdown corpus files found in: {source_path}")

    requests: list[tuple[Path, IngestPipelineRequest]] = []
    for corpus_path in corpus_paths:
        raw_text = corpus_path.read_text(encoding="utf-8")
        title = corpus_path.stem.replace("-", " ").replace("_", " ").strip() or corpus_path.stem
        request = IngestPipelineRequest(
            workspace_id=args.workspace,
            source_uri=corpus_path.as_uri(),
            title=title,
            raw_text=raw_text,
            source_format=args.source_format,
            operation_mode=getattr(args, "operation_mode", "parse_first"),
            parser_mode=args.parser_mode,
            parser_lane=args.parser_lane,
            promotion_mode=args.promotion_mode,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
        )
        requests.append((corpus_path, request))
    return requests


def _cmd_demo(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
    from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
    from kogwistar_llm_wiki.namespaces import GraphSpace
    from kogwistar_llm_wiki.worker import MaintenanceWorker

    request_items = _read_demo_requests_from_source(args)
    vault_root = Path(args.vault).expanduser().resolve()
    vault_root.mkdir(parents=True, exist_ok=True)

    engines = _build_demo_engines(
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
    )
    try:
        pipeline = IngestPipeline(
            engines,
            debug_run_dir=args.debug_run_dir,
            **_conversation_persistence_kwargs(args),
        )
        materialize_maintenance_designs(engines.workflow)
        artifacts_by_source: list[dict[str, object]] = []
        last_source_path: Path | None = None
        last_artifacts = None
        for source_path, request in request_items:
            last_source_path = source_path
            last_artifacts = pipeline.run(request)
            artifacts_by_source.append(
                {
                    "source": str(source_path),
                    "artifacts": asdict(last_artifacts),
                }
            )
        MaintenanceWorker(engines).process_pending_jobs(args.workspace)
        vault_result = pipeline.build_obsidian_vault(
            vault_root,
            workspace_id=args.workspace,
            graph_spaces=[GraphSpace.BASE_KG],
            projection_filter="demo",
        )

        print(
            json.dumps(
                {
                    "workspace_id": args.workspace,
                    "source": str(last_source_path) if last_source_path is not None else str(args.source),
                    "vault": str(vault_root),
                    "mode": "demo-memory-single-process",
                    "corpus_mode": "directory" if len(request_items) > 1 else "single-file",
                    "corpus_count": len(request_items),
                    "artifacts": asdict(last_artifacts) if last_artifacts is not None else None,
                    "artifacts_by_source": artifacts_by_source,
                    "vault_result": {
                        **asdict(vault_result),
                        "vault_root": str(vault_result.vault_root),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        _close_engines(engines)


def _cmd_ingest(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline

    source_path, request = _read_request_from_source(args)
    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
    )
    try:
        pipeline = IngestPipeline(
            engines,
            debug_run_dir=args.debug_run_dir,
            **_conversation_persistence_kwargs(args),
        )
        artifacts = pipeline.run(request)
        print(
            json.dumps(
                {
                    "workspace_id": args.workspace,
                    "source": str(source_path),
                    "artifacts": asdict(artifacts),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        _close_engines(engines)


def _cmd_report(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.inspection import (
        build_workspace_graph_artifact_dump,
        build_workspace_quality_report,
    )

    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
    )
    try:
        report = build_workspace_quality_report(
            engines,
            workspace_id=args.workspace,
            report_scope=args.report_scope,
        )
        artifact_dump = None
        if args.dump_mode in {"raw", "both"}:
            artifact_dump = build_workspace_graph_artifact_dump(
                engines,
                workspace_id=args.workspace,
                report_scope=args.report_scope,
                raw_limit_per_space=args.dump_limit_per_space,
            )
        print(
            json.dumps(
                {
                    "workspace_id": report.workspace_id,
                    "report_scope": report.report_scope,
                    "dump_mode": args.dump_mode,
                    "graph_quality": report.graph_quality,
                    "graph_health_score": report.graph_health_score,
                    "graph_spaces": [asdict(item) for item in report.graph_spaces],
                    "review_artifact_counts": report.review_artifact_counts,
                    "maintenance_patch_report": asdict(report.maintenance_patch_report),
                    "sample_nodes": [asdict(item) for item in report.sample_nodes],
                    "sample_edges": [asdict(item) for item in report.sample_edges],
                    "notes": list(report.notes),
                    **(
                        {
                            "raw_node_count": artifact_dump.node_count,
                            "raw_edge_count": artifact_dump.edge_count,
                            "raw_nodes": [asdict(item) for item in artifact_dump.raw_nodes],
                            "raw_edges": [asdict(item) for item in artifact_dump.raw_edges],
                        }
                        if artifact_dump is not None
                        else {}
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        _close_engines(engines)


def _cmd_daemon_projection(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.daemon import ProjectionDaemon

    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
    )
    Path(args.vault).expanduser().resolve().mkdir(parents=True, exist_ok=True)
    daemon = ProjectionDaemon(
        engines=engines,
        workspace_id=args.workspace,
        vault_root=args.vault,
        poll_interval=args.interval,
    )

    def _stop(sig, frame) -> None:  # noqa: ANN001
        logger.info("Received signal %s — graceful stop requested for ProjectionDaemon", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    daemon.run()


def _cmd_daemon_maintenance(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.daemon import MaintenanceDaemon

    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
    )
    daemon = MaintenanceDaemon(
        engines=engines,
        workspace_id=args.workspace,
        poll_interval=args.interval,
    )

    def _stop(sig, frame) -> None:  # noqa: ANN001
        logger.info("Received signal %s — graceful stop requested for MaintenanceDaemon", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    daemon.run()


def _cmd_workbench(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.codex_workbench_agent import CodexCliCockpitResponder, CodexCliSettings, HostCockpitResponder
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
    from kogwistar_llm_wiki.workbench_api import WorkbenchApi
    from kogwistar_llm_wiki.workbench_http import serve_workbench

    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
    )
    def trace_line(line: str) -> None:
        logger.info("workbench_cockpit_trace %s", line)
    callback_url = os.environ.get("LLM_WIKI_COCKPIT_CALLBACK_URL", "").strip()
    if callback_url:
        allowed_hosts = os.environ.get(
            "LLM_WIKI_COCKPIT_CALLBACK_ALLOWED_HOSTS",
            "host.docker.internal,localhost,127.0.0.1",
        ).split(",")
        responder = HostCockpitResponder(
            callback_url,
            allowed_hosts=allowed_hosts,
            token=os.environ.get("LLM_WIKI_COCKPIT_CALLBACK_TOKEN"),
            timeout_seconds=max(1.0, float(os.environ.get("LLM_WIKI_COCKPIT_CALLBACK_TIMEOUT_SECONDS", args.codex_timeout))),
            trace_line=trace_line,
        )
    else:
        responder = CodexCliCockpitResponder(
            CodexCliSettings(
                executable=args.codex_executable,
                model=args.codex_model,
                profile=args.codex_profile,
                timeout_seconds=args.codex_timeout,
                transport=getattr(args, "codex_transport", None)
                or os.environ.get("KOGWISTAR_CODEX_TRANSPORT", "exec"),
            ),
            trace_line=trace_line,
        )
    api = WorkbenchApi(
        IngestPipeline(engines, **_conversation_persistence_kwargs(args)),
        cockpit_responder=responder,
        codex_worker_count=args.codex_workers,
        trace_sink=lambda event: logger.info("workbench_worker_trace %s", json.dumps(event, sort_keys=True)),
    )
    api.recover_interactions(args.workspace)
    logger.info(
        "workbench_started workspace=%s host=%s port=%s codex_model=%s workers=%s",
        args.workspace,
        args.host,
        args.port,
        args.codex_model or "codex-default",
        args.codex_workers,
    )
    try:
        serve_workbench(api, host=args.host, port=args.port)
    finally:
        _close_engines(engines)


def _cmd_mcp(args: argparse.Namespace) -> None:
    """Serve the full MCP protocol through the optional FastMCP dependency."""
    from kogwistar_llm_wiki.agent_gateway import AgentGateway
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
    from kogwistar_llm_wiki.mcp_agent_server import build_agent_mcp
    from kogwistar_llm_wiki.workbench_api import WorkbenchApi

    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
    )
    try:
        gateway = AgentGateway(WorkbenchApi(IngestPipeline(engines, **_conversation_persistence_kwargs(args))))
        mcp = build_agent_mcp(gateway)
        logger.info("agent_mcp_started workspace=%s transport=%s host=%s port=%s", args.workspace, args.transport, args.host, args.port)
        run_kwargs: dict[str, object] = {"transport": args.transport}
        if args.transport != "stdio":
            run_kwargs.update({"host": args.host, "port": args.port, "path": args.path})
        mcp.run(**run_kwargs)
    finally:
        _close_engines(engines)


def _cmd_embedding_service(args: argparse.Namespace) -> None:
    """Run the optional isolated multimodal Embedding Service."""
    from llm_wiki_embedding_service.__main__ import main as run_service

    del args
    run_service()


def _cmd_seed_bundle(args: argparse.Namespace) -> None:
    """Seed, optionally inspect through cockpit mode, and export a graph bundle."""

    from kogwistar_llm_wiki.codex_workbench_agent import CodexCliCockpitResponder, CodexCliSettings
    from kogwistar_llm_wiki.graph_seed_bundle import (
        dump_seed_bundle,
        export_graph_seed_bundle,
        load_seed_bundle,
        seed_graph_bundle,
    )
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
    from kogwistar_llm_wiki.workbench_api import WorkbenchApi

    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **_conversation_persistence_kwargs(args),
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
                IngestPipeline(engines, **_conversation_persistence_kwargs(args)),
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
            cockpit_path.write_text(
                json.dumps(cockpit_response, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
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
        _close_engines(engines)


def _cmd_archive_create(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.archive import create_archive

    engines = _build_engines(
        args.workspace, args.data_dir, args.backend, args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
    )
    try:
        manifest = create_archive(
            engines,
            workspace_id=args.workspace,
            output=args.output,
            data_dir=args.data_dir,
            parent_archive=args.parent,
            include_backend_snapshot=args.include_backend_snapshot,
            backend=args.backend,
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
    finally:
        _close_engines(engines)


def _cmd_archive_inspect(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.archive import inspect_archive

    print(json.dumps(inspect_archive(args.archive), indent=2, sort_keys=True))


def _cmd_archive_verify(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.archive import verify_archive

    print(json.dumps(verify_archive(args.archive), indent=2, sort_keys=True))


def _cmd_archive_restore(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.archive import inspect_archive, restore_archive, restore_backend_snapshot

    source_workspace = str(inspect_archive(args.archive)["workspace_id"])
    if args.use_backend_snapshot:
        if not args.data_dir:
            raise ValueError("--data-dir is required with --use-backend-snapshot")
        if not args.apply:
            # Snapshot validation must never create directories or write files.
            # The explicit flag mirrors portable event restore safety.
            result = restore_backend_snapshot(
                archive=args.archive,
                target_data_dir=args.data_dir or "",
                backend=args.backend,
                embedding_fingerprint=args.embedding_fingerprint,
                apply=False,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return
        if args.target_workspace and args.target_workspace != source_workspace:
            raise ValueError("--use-backend-snapshot supports exact workspace recovery only")
        result = restore_backend_snapshot(
            archive=args.archive,
            target_data_dir=args.data_dir,
            backend=args.backend,
            embedding_fingerprint=args.embedding_fingerprint,
            apply=True,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    target_workspace = args.target_workspace or source_workspace
    engines = _build_engines(
        target_workspace, args.data_dir, args.backend, args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
    )
    try:
        report = restore_archive(
            engines,
            archive=args.archive,
            parent_archives=args.parent,
            target_workspace_id=args.target_workspace,
            apply=args.apply,
            target_data_dir=args.data_dir,
        )
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    finally:
        _close_engines(engines)


def _cmd_archive_catalog(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.archive import inspect_archive

    rows: list[dict[str, object]] = []
    for path in sorted(Path(args.directory).expanduser().resolve().glob("*.tar.gz")):
        try:
            manifest = inspect_archive(path)
        except Exception as exc:  # noqa: BLE001
            rows.append({"path": str(path), "status": "invalid", "error": str(exc)})
            continue
        if args.before_ms is not None and int(manifest.get("captured_at_ms", 0)) > args.before_ms:
            continue
        rows.append({
            "path": str(path), "status": "complete", "archive_id": manifest.get("archive_id"),
            "captured_at_ms": manifest.get("captured_at_ms"), "archive_kind": manifest.get("archive_kind"),
        })
    rows.sort(key=lambda item: int(item.get("captured_at_ms") or 0))
    print(json.dumps(rows, indent=2, sort_keys=True))


def _namespace_engine_items(engines: "NamespaceEngines"):
    return (
        ("conversation", engines.conversation),
        ("workflow", engines.workflow),
        ("knowledge", engines.kg),
        ("wisdom", engines.wisdom),
        ("derived_knowledge", engines.derived_knowledge),
    )


def _cmd_embeddings_inspect(args: argparse.Namespace) -> None:
    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        conversation_persistence_mode=args.conversation_persistence_mode,
        embedding_profile_mode="inspect",
    )
    try:
        print(
            json.dumps(
                {
                    label: engine.embedding_profile_report
                    for label, engine in _namespace_engine_items(engines)
                    if engine is not None
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        _close_engines(engines)


def _cmd_embeddings_adopt_legacy(args: argparse.Namespace) -> None:
    if not args.acknowledge_legacy_vectors:
        raise ValueError(
            "legacy profile adoption is unsafe without --acknowledge-legacy-vectors; "
            "verify the previous provider, model, dimension, endpoint, and metric first"
        )
    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        conversation_persistence_mode=args.conversation_persistence_mode,
        embedding_profile_mode="adopt",
    )
    try:
        print(
            json.dumps(
                {
                    label: engine.embedding_profile_report
                    for label, engine in _namespace_engine_items(engines)
                    if engine is not None
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        _close_engines(engines)


def _compose_options_from_args(args: argparse.Namespace) -> ComposeOptions:
    return ComposeOptions(
        backend=args.backend,
        workspace=args.workspace,
        project_name=args.project_name,
        mode=args.mode,
        embedding_backend=args.embedding_backend,
        with_otel=args.with_otel,
        with_oauth=args.with_oauth,
        auth_mode=args.auth_mode,
        model_revision=args.model_revision,
        embedding_dimension=args.embedding_dimension,
    )


def _cmd_compose_generate(args: argparse.Namespace) -> None:
    path = write_compose(args.output, _compose_options_from_args(args))
    print(json.dumps({"status": "generated", "path": str(path.resolve())}, indent=2))


def _cmd_compose_check(args: argparse.Namespace) -> None:
    path = Path(args.file)
    result = check_compose_text(path.read_text(encoding="utf-8"))
    docker_check: dict[str, object] = {"status": "not_run", "detail": "docker command unavailable"}
    try:
        completed = subprocess.run(
            ["docker", "compose", "-f", str(path), "config", "--quiet"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            check=False,
        )
    except OSError as exc:
        docker_check["detail"] = f"docker compose check skipped: {exc}"
    else:
        if completed.returncode == 0:
            docker_check = {"status": "passed"}
        else:
            docker_check = {
                "status": "failed",
                "detail": (completed.stderr or completed.stdout).strip()[:2000],
            }
            result["valid"] = False
            errors = result.setdefault("errors", [])
            if isinstance(errors, list):
                errors.append("docker compose config rejected the file")
    result["docker_compose"] = docker_check
    print(json.dumps({"file": str(path), **result}, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(78)


def main(argv: list[str] | None = None) -> int:
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
    demo_p.set_defaults(func=_cmd_demo)

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
    ingest_p.set_defaults(func=_cmd_ingest)

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
    report_p.set_defaults(func=_cmd_report)

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
    workbench_p.set_defaults(func=_cmd_workbench)

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
    mcp_p.set_defaults(func=_cmd_mcp)

    embedding_p = sub.add_parser(
        "embedding-service",
        aliases=["embedding-service"],
        help="Serve one isolated Qwen3-VL embedding profile",
    )
    embedding_p.set_defaults(func=_cmd_embedding_service)

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
    seed_p.set_defaults(func=_cmd_seed_bundle)

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
    archive_create_p.set_defaults(func=_cmd_archive_create)

    for name, handler in (("inspect", _cmd_archive_inspect), ("verify", _cmd_archive_verify)):
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
    archive_restore_p.set_defaults(func=_cmd_archive_restore)

    archive_catalog_p = archive_sub.add_parser("catalog", help="List verified archives in a directory")
    archive_catalog_p.add_argument("--directory", required=True, help="Archive directory")
    archive_catalog_p.add_argument("--before-ms", type=int, default=None, help="Only show archives captured by this epoch-millisecond")
    archive_catalog_p.set_defaults(func=_cmd_archive_catalog)

    embeddings_p = sub.add_parser(
        "embeddings",
        help="Inspect or explicitly adopt persistent embedding profiles",
    )
    embeddings_sub = embeddings_p.add_subparsers(dest="embedding_command", required=True)
    embedding_inspect_p = embeddings_sub.add_parser(
        "inspect", help="Report configured, registered, and physical embedding state"
    )
    embedding_inspect_p.add_argument("--workspace", required=True, help="Workspace ID")
    embedding_inspect_p.set_defaults(func=_cmd_embeddings_inspect)
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
    embedding_adopt_p.set_defaults(func=_cmd_embeddings_adopt_legacy)

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
    compose_generate_p.set_defaults(func=_cmd_compose_generate)
    compose_check_p = compose_sub.add_parser("check", help="Validate a generated or checked-in Compose YAML")
    compose_check_p.add_argument("--file", required=True, help="Compose YAML path")
    compose_check_p.set_defaults(func=_cmd_compose_check)

    # daemon sub-command
    daemon_p = sub.add_parser("daemon", help="Run a background daemon")
    daemon_sub = daemon_p.add_subparsers(dest="daemon_type", required=True)

    proj_p = daemon_sub.add_parser("projection", help="Obsidian projection daemon")
    proj_p.add_argument("--workspace", required=True, help="Workspace ID")
    proj_p.add_argument("--vault", required=True, help="Obsidian vault root path")
    proj_p.add_argument("--interval", type=float, default=5.0, help="Poll interval (seconds)")
    proj_p.set_defaults(func=_cmd_daemon_projection)

    maint_p = daemon_sub.add_parser("maintenance", help="Maintenance distillation daemon")
    maint_p.add_argument("--workspace", required=True, help="Workspace ID")
    maint_p.add_argument("--interval", type=float, default=10.0, help="Poll interval (seconds)")
    maint_p.set_defaults(func=_cmd_daemon_maintenance)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
