"""CLI entry point: ``python -m kogwistar_llm_wiki``

Sub-commands
------------
demo --workspace <id> --source <path> --vault <path> [--title <text>]
    Run the ephemeral end-to-end demo in one process. Uses the in-memory
    engine bundle, then writes the Obsidian vault to disk before exiting.

ingest --workspace <id> --source <path> [--title <text>] [--promotion-mode <mode>]
    Read a source document and populate the workspace state.

daemon projection --workspace <id> --vault <path> [--interval <s>]
    Run the Obsidian projection daemon (blocking).

daemon maintenance --workspace <id> [--interval <s>]
    Run the maintenance distillation daemon (blocking).

``demo`` is intentionally single-process and ephemeral so it does not depend on
any process-shared local backend.

The persistent commands expect KOGWISTAR_DATA_DIR (or --data-dir) to point at a
directory containing the local backend state. Use ``--backend postgres`` and
``--dsn`` to switch to a PostgreSQL/pgvector-backed store.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from dataclasses import asdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("kogwistar_llm_wiki")


def _build_engines(
    workspace_id: str,
    data_dir: str | None,
    backend: str,
    dsn: str | None,
    *,
    split_derived_knowledge: bool = False,
):
    """Construct a NamespaceEngines bundle from the selected backend."""
    from kogwistar_llm_wiki.ingest_pipeline import (
        build_persistent_namespace_engines,
        build_postgres_namespace_engines,
    )

    if data_dir is None:
        raise ValueError("--data-dir is required for the llm-wiki CLI")
    if backend == "chroma":
        return build_persistent_namespace_engines(
            base_dir=data_dir,
            split_derived_knowledge=split_derived_knowledge,
        )
    if backend == "postgres":
        if not dsn:
            raise ValueError("--dsn is required when --backend postgres is selected")
        return build_postgres_namespace_engines(
            base_dir=data_dir,
            dsn=dsn,
            split_derived_knowledge=split_derived_knowledge,
        )
    raise ValueError(f"Unsupported backend: {backend!r}")


def _build_demo_engines(*, split_derived_knowledge: bool = False):
    from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines

    return build_in_memory_namespace_engines(split_derived_knowledge=split_derived_knowledge)


def _read_request_from_source(args: argparse.Namespace):
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
        parser_mode=args.parser_mode,
        promotion_mode=args.promotion_mode,
    )
    return source_path, request


def _cmd_demo(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
    from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
    from kogwistar_llm_wiki.models import IngestPipelineArtifacts
    from kogwistar_llm_wiki.worker import MaintenanceWorker

    source_path, request = _read_request_from_source(args)
    vault_root = Path(args.vault).expanduser().resolve()
    vault_root.mkdir(parents=True, exist_ok=True)

    engines = _build_demo_engines(split_derived_knowledge=args.split_derived_knowledge)
    pipeline = IngestPipeline(engines)
    ns = pipeline.namespaces_for(args.workspace)
    source_document_id = pipeline._source_document_id(request)
    pipeline.register_source(
        request=request,
        source_document_id=source_document_id,
        namespace=ns.conv_fg,
    )
    parse_result = pipeline.parse_source(
        request=request,
        source_document_id=source_document_id,
    )
    graph_extraction = pipeline.translate_parse_result(
        parse_result=parse_result,
        source_document_id=source_document_id,
    )
    pipeline.persist_demo_graph_extraction(
        request=request,
        source_document_id=source_document_id,
        graph_extraction=graph_extraction,
        namespace=ns.kg,
    )
    materialize_maintenance_designs(engines.workflow)
    pipeline.ingest_parse_result(
        request=request,
        source_document_id=source_document_id,
        graph_extraction=graph_extraction,
        namespace=ns.conv_fg,
    )
    maintenance_job_id = pipeline.create_maintenance_request(
        request=request,
        source_document_id=source_document_id,
        namespace=ns.conv_bg,
    )
    candidate_link_id = pipeline.create_candidate_link(
        request=request,
        source_document_id=source_document_id,
        parse_result=parse_result,
        namespace=ns.conv_bg,
    )
    promotion_candidate_id = pipeline.create_promotion_candidate(
        request=request,
        source_document_id=source_document_id,
        candidate_link_id=candidate_link_id,
        namespace=ns.conv_bg,
    )
    artifacts = IngestPipelineArtifacts(
        source_document_id=source_document_id,
        maintenance_job_id=maintenance_job_id,
        candidate_link_id=candidate_link_id,
        promotion_candidate_id=promotion_candidate_id,
        promoted_entity_id=None,
    )
    MaintenanceWorker(engines).process_pending_jobs(args.workspace)
    vault_result = pipeline.build_obsidian_vault(vault_root, workspace_id=args.workspace)

    print(
        json.dumps(
            {
                "workspace_id": args.workspace,
                "source": str(source_path),
                "vault": str(vault_root),
                "mode": "demo-memory-single-process",
                "artifacts": asdict(artifacts),
                "vault_result": {
                    **asdict(vault_result),
                    "vault_root": str(vault_result.vault_root),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


def _cmd_ingest(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline

    source_path, request = _read_request_from_source(args)
    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
    )
    pipeline = IngestPipeline(engines)
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


def _cmd_daemon_projection(args: argparse.Namespace) -> None:
    from kogwistar_llm_wiki.daemon import ProjectionDaemon

    engines = _build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
    )
    Path(args.vault).expanduser().resolve().mkdir(parents=True, exist_ok=True)
    daemon = ProjectionDaemon(
        engines=engines,
        workspace_id=args.workspace,
        vault_root=args.vault,
        poll_interval=args.interval,
    )

    def _stop(sig, frame):  # noqa: ANN001
        logger.info("Received signal %s — stopping ProjectionDaemon…", sig)
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
    )
    daemon = MaintenanceDaemon(
        engines=engines,
        workspace_id=args.workspace,
        poll_interval=args.interval,
    )

    def _stop(sig, frame):  # noqa: ANN001
        logger.info("Received signal %s — stopping MaintenanceDaemon…", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    daemon.run()


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
        default=None,
        help="PostgreSQL DSN for --backend postgres",
    )
    parser.add_argument(
        "--split-derived-knowledge",
        action="store_true",
        help="Host derived knowledge on a dedicated engine instead of reusing raw KG",
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
        "--parser-mode",
        choices=["heuristic", "ollama", "gemini"],
        default="heuristic",
        help="Parser mode to use for the document",
    )
    demo_p.add_argument(
        "--promotion-mode",
        choices=["pending", "sync"],
        default="sync",
        help="Whether to auto-promote the extracted knowledge",
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
        "--parser-mode",
        choices=["heuristic", "ollama", "gemini"],
        default="heuristic",
        help="Parser mode to use for the document",
    )
    ingest_p.add_argument(
        "--promotion-mode",
        choices=["pending", "sync"],
        default="sync",
        help="Whether to auto-promote the extracted knowledge",
    )
    ingest_p.set_defaults(func=_cmd_ingest)

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
