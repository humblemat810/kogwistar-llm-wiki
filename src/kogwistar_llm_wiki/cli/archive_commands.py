"""Portable archive commands for the LLM-Wiki CLI."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from ..models import NamespaceEngines


def archive_create(
    args: argparse.Namespace,
    *,
    build_engines: Callable[..., NamespaceEngines],
    close_engines: Callable[[NamespaceEngines], None],
) -> None:
    from ..archive import create_archive

    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
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
        close_engines(engines)


def archive_inspect(args: argparse.Namespace) -> None:
    from ..archive import inspect_archive

    print(json.dumps(inspect_archive(args.archive), indent=2, sort_keys=True))


def archive_verify(args: argparse.Namespace) -> None:
    from ..archive import verify_archive

    print(json.dumps(verify_archive(args.archive), indent=2, sort_keys=True))


def archive_restore(
    args: argparse.Namespace,
    *,
    build_engines: Callable[..., NamespaceEngines],
    close_engines: Callable[[NamespaceEngines], None],
) -> None:
    from ..archive import inspect_archive, restore_archive, restore_backend_snapshot

    source_workspace = str(inspect_archive(args.archive)["workspace_id"])
    if args.use_backend_snapshot:
        if not args.data_dir:
            raise ValueError("--data-dir is required with --use-backend-snapshot")
        if not args.apply:
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
    engines = build_engines(
        target_workspace,
        args.data_dir,
        args.backend,
        args.dsn,
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
        close_engines(engines)


def archive_catalog(args: argparse.Namespace) -> None:
    from ..archive import inspect_archive

    rows: list[dict[str, object]] = []
    for path in sorted(Path(args.directory).expanduser().resolve().glob("*.tar.gz")):
        try:
            manifest = inspect_archive(path)
        except Exception as exc:  # noqa: BLE001
            rows.append({"path": str(path), "status": "invalid", "error": str(exc)})
            continue
        if args.before_ms is not None and int(manifest.get("captured_at_ms", 0)) > args.before_ms:
            continue
        rows.append(
            {
                "path": str(path),
                "status": "complete",
                "archive_id": manifest.get("archive_id"),
                "captured_at_ms": manifest.get("captured_at_ms"),
                "archive_kind": manifest.get("archive_kind"),
            }
        )
    rows.sort(key=lambda item: int(item.get("captured_at_ms") or 0))
    print(json.dumps(rows, indent=2, sort_keys=True))
