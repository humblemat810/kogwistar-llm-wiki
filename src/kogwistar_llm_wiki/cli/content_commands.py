"""Content-oriented command implementations for the LLM-Wiki CLI."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kogwistar_llm_wiki.models import IngestPipelineRequest


def read_request_from_source(args: argparse.Namespace) -> tuple[Path, IngestPipelineRequest]:
    """Build an ingest request from one local source file."""
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


def read_demo_requests_from_source(
    args: argparse.Namespace,
) -> list[tuple[Path, IngestPipelineRequest]]:
    """Build demo requests from one file or a bounded Markdown corpus."""
    from kogwistar_llm_wiki.models import IngestPipelineRequest

    source_path = Path(args.source).expanduser().resolve()
    if source_path.is_file():
        _, request = read_request_from_source(args)
        return [(source_path, request)]

    if not source_path.is_dir():
        raise FileNotFoundError(f"source path not found: {source_path}")

    corpus_paths = sorted(
        path
        for path in source_path.glob("*.md")
        if path.is_file() and path.name not in {"index.md", "manifest.md"}
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


def run_demo(
    args: argparse.Namespace,
    *,
    build_demo_engines: Callable[..., object],
    close_engines: Callable[[object], None],
    persistence_kwargs: Callable[[argparse.Namespace], dict[str, str]],
) -> None:
    """Run the ephemeral, single-process demonstration flow."""
    from kogwistar_llm_wiki.configuration.workspace import GraphSpace
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
    from kogwistar_llm_wiki.maintenance.maintenance_designs import (
        materialize_maintenance_designs,
    )
    from kogwistar_llm_wiki.worker import MaintenanceWorker

    request_items = read_demo_requests_from_source(args)
    vault_root = Path(args.vault).expanduser().resolve()
    vault_root.mkdir(parents=True, exist_ok=True)

    engines = build_demo_engines(
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
    )
    try:
        pipeline = IngestPipeline(
            engines,
            debug_run_dir=args.debug_run_dir,
            **persistence_kwargs(args),
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
        close_engines(engines)


def run_ingest(
    args: argparse.Namespace,
    *,
    build_engines: Callable[..., object],
    close_engines: Callable[[object], None],
    persistence_kwargs: Callable[[argparse.Namespace], dict[str, str]],
) -> None:
    """Run one persistent ingest command."""
    from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline

    source_path, request = read_request_from_source(args)
    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
    )
    try:
        pipeline = IngestPipeline(
            engines,
            debug_run_dir=args.debug_run_dir,
            **persistence_kwargs(args),
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
        close_engines(engines)


def run_report(
    args: argparse.Namespace,
    *,
    build_engines: Callable[..., object],
    close_engines: Callable[[object], None],
    persistence_kwargs: Callable[[argparse.Namespace], dict[str, str]],
) -> None:
    """Build and print a persisted workspace quality report."""
    from kogwistar_llm_wiki.workbench.inspection import (
        build_workspace_graph_artifact_dump,
        build_workspace_quality_report,
    )

    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        **persistence_kwargs(args),
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
        close_engines(engines)
