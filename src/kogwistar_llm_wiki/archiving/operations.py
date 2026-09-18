"""Archive creation and restore orchestration.

The root module remains a compatibility facade; archive mechanics live in this
domain package.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from kogwistar.engine_core.event_envelope import EntityEventEnvelope

from ..models import NamespaceEngines
from ..utils import _temporary_namespace
from .archive_contracts import ARCHIVE_FORMAT_VERSION, ArchiveError, RestoreReport
from .io import (
    artifact_files as _artifact_files,
)
from .io import (
    assert_quiescent,
    workspace_archive_namespaces,
)
from .io import (
    assert_target_empty as _assert_target_empty,
)
from .io import (
    embedding_fingerprint as _embedding_fingerprint,
)
from .io import (
    embedding_profiles as _embedding_profiles,
)
from .io import (
    event_writer as _event_writer,
)
from .io import (
    restore_artifacts as _restore_artifacts,
)
from .io import (
    safe_relative_member as _safe_relative_member,
)
from .io import (
    sha256_file as _sha256_file,
)
from .validation import canonical_event_line as _canonical_event_line
from .validation import event_reader as _event_reader
from .validation import inspect_archive, verify_archive
from .validation import load_chain as _load_chain


def create_archive(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    output: str | Path,
    data_dir: str | Path | None = None,
    parent_archive: str | Path | None = None,
    include_backend_snapshot: bool = False,
    require_quiescent: bool = True,
    backend: str | None = None,
) -> dict[str, Any]:
    """Create a full or incremental archive at fixed namespace watermarks."""
    if require_quiescent:
        assert_quiescent(engines)
    parent_manifest = inspect_archive(parent_archive) if parent_archive else None
    specs = workspace_archive_namespaces(engines, workspace_id)
    watermarks: dict[str, int] = {}
    ranges: dict[str, dict[str, int]] = {}
    for spec in specs:
        meta = getattr(spec.engine, "meta_sqlite", None)
        getter = getattr(meta, "get_latest_entity_event_seq", None)
        if not callable(getter):
            raise ArchiveError(f"metadata store {type(meta).__name__} lacks event watermark support")
        end = int(getter(namespace=spec.namespace) or 0)
        start = int((parent_manifest or {}).get("watermarks", {}).get(spec.namespace, 0)) + 1
        if end < start - 1:
            raise ArchiveError(f"event history for {spec.namespace!r} is behind parent watermark")
        watermarks[spec.namespace] = end
        ranges[spec.namespace] = {"from_seq": start, "to_seq": end}

    archive_id = f"llm-wiki-archive-{uuid.uuid4()}"
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="llm-wiki-archive-") as temp_name:
        staging = Path(temp_name)
        events_path = staging / "events.jsonl"
        count = 0
        digest = hashlib.sha256()
        with events_path.open("wb") as stream:
            for spec in specs:
                start = ranges[spec.namespace]["from_seq"]
                end = ranges[spec.namespace]["to_seq"]
                reader = _event_reader(spec.engine.meta_sqlite)
                expected = start
                for event in reader(namespace=spec.namespace, from_seq=start, to_seq=end):
                    if event.seq != expected:
                        raise ArchiveError(f"event history has a gap in {spec.namespace!r}")
                    line = _canonical_event_line(event)
                    stream.write(line)
                    digest.update(line)
                    count += 1
                    expected += 1
                if expected != end + 1:
                    raise ArchiveError(f"watermark {end} is not fully readable for {spec.namespace!r}")

        artifact_entries: list[dict[str, Any]] = []
        if data_dir is not None:
            root = Path(data_dir).expanduser().resolve()
            for source, arcname in _artifact_files(root):
                target = staging / arcname
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                artifact_entries.append({"path": arcname, "sha256": _sha256_file(target)})

        snapshot_entries: list[dict[str, Any]] = []
        if include_backend_snapshot:
            seen_paths: set[Path] = set()
            for spec in specs:
                persist = getattr(spec.engine, "persist_directory", None)
                if not persist:
                    continue
                source = Path(persist).expanduser().resolve()
                if source in seen_paths or not source.exists():
                    continue
                seen_paths.add(source)
                root = Path(data_dir).expanduser().resolve() if data_dir is not None else None
                relative = (
                    source.relative_to(root).as_posix()
                    if root is not None and source.is_relative_to(root)
                    else spec.label
                )
                arcname = Path("backend_snapshots") / Path(relative)
                shutil.copytree(source, staging / arcname, dirs_exist_ok=True)
                snapshot_files = []
                copied_root = staging / arcname
                for copied in copied_root.rglob("*"):
                    if copied.is_file() and not copied.is_symlink():
                        snapshot_files.append(
                            {
                                "path": copied.relative_to(staging).as_posix(),
                                "sha256": _sha256_file(copied),
                            }
                        )
                snapshot_entries.append({"label": spec.label, "path": arcname.as_posix(), "relative_path": relative, "files": snapshot_files})

        profiles = _embedding_profiles(engines)
        manifest = {
            "archive_format_version": ARCHIVE_FORMAT_VERSION,
            "archive_id": archive_id,
            "archive_kind": "incremental" if parent_manifest else "base",
            "parent_archive_id": parent_manifest.get("archive_id") if parent_manifest else None,
            "workspace_id": workspace_id,
            "captured_at_ms": int(__import__("time").time() * 1000),
            "watermarks": watermarks,
            "ranges": ranges,
            "event_count": count,
            "events_sha256": digest.hexdigest(),
            "artifact_entries": artifact_entries,
            "backend_snapshot_entries": snapshot_entries,
            "quiescent_capture_required": require_quiescent,
            "authoritative_source": "kogwistar.entity_events",
            "backend": backend,
            "embedding_profiles": profiles,
            "embedding_fingerprint": _embedding_fingerprint(profiles),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(staging / "manifest.json", arcname="manifest.json")
            archive.add(events_path, arcname="events.jsonl")
            if (staging / "artifacts").exists():
                archive.add(staging / "artifacts", arcname="artifacts")
            if (staging / "backend_snapshots").exists():
                archive.add(staging / "backend_snapshots", arcname="backend_snapshots")
    return manifest


def _remap_value(value: Any, *, old_workspace: str, new_workspace: str, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {str(k): _remap_value(v, old_workspace=old_workspace, new_workspace=new_workspace, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap_value(item, old_workspace=old_workspace, new_workspace=new_workspace, key=key) for item in value]
    if isinstance(value, str) and key in {"workspace_id", "workspace", "namespace", "source_namespace", "projection_namespace", "graph_namespace"}:
        if key in {"workspace_id", "workspace"} and value == old_workspace:
            return new_workspace
        prefix = f"ws:{old_workspace}:"
        if value == prefix[:-1] or value.startswith(prefix):
            return f"ws:{new_workspace}:" + value[len(prefix):]
    return value


def _remapped_event(event: EntityEventEnvelope, *, old_workspace: str, new_workspace: str) -> EntityEventEnvelope:
    payload = json.loads(event.payload_json)
    payload = _remap_value(payload, old_workspace=old_workspace, new_workspace=new_workspace)
    return EntityEventEnvelope(
        namespace=_remap_namespace(event.namespace, old_workspace=old_workspace, new_workspace=new_workspace),
        seq=event.seq,
        event_id=event.event_id,
        entity_kind=event.entity_kind,
        entity_id=event.entity_id,
        op=event.op,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        created_at=event.created_at,
    )


def _remap_namespace(namespace: str, *, old_workspace: str, new_workspace: str) -> str:
    prefix = f"ws:{old_workspace}:"
    if namespace.startswith(prefix):
        return f"ws:{new_workspace}:" + namespace[len(prefix):]
    return namespace


def restore_archive(
    engines: NamespaceEngines,
    *,
    archive: str | Path,
    parent_archives: Iterable[str | Path] = (),
    target_workspace_id: str | None = None,
    apply: bool = False,
    target_data_dir: str | Path | None = None,
) -> RestoreReport:
    """Validate, and optionally apply, an archive to an empty target bundle."""
    manifest, source_events = _load_chain(archive, parent_archives)
    source_workspace = str(manifest["workspace_id"])
    target_workspace = target_workspace_id or source_workspace
    remap = target_workspace != source_workspace
    target_specs = {spec.namespace: spec for spec in workspace_archive_namespaces(engines, target_workspace)}
    events: list[EntityEventEnvelope] = []
    for event in source_events:
        transformed = _remapped_event(event, old_workspace=source_workspace, new_workspace=target_workspace) if remap else event
        if transformed.namespace not in target_specs:
            raise ArchiveError(f"archive namespace {transformed.namespace!r} is not supported by target")
        events.append(transformed)

    grouped: dict[str, list[EntityEventEnvelope]] = {}
    for event in events:
        grouped.setdefault(event.namespace, []).append(event)
    for namespace, rows in grouped.items():
        rows.sort(key=lambda item: item.seq)
        expected = 1
        for event in rows:
            if event.seq != expected:
                raise ArchiveError(f"restore requires a complete event range in {namespace!r}")
            expected += 1
        existing = list(_event_reader(target_specs[namespace].engine.meta_sqlite)(
            namespace=namespace, from_seq=1, to_seq=1
        ))
        if existing:
            raise ArchiveError(f"restore target namespace {namespace!r} is not empty")

    _assert_target_empty(target_specs)

    if not apply:
        return RestoreReport(manifest["archive_id"], source_workspace, target_workspace, True, len(events), 0, False)

    if target_data_dir is not None:
        data_root = Path(target_data_dir).expanduser().resolve()
        data_root.mkdir(parents=True, exist_ok=True)
        previous_artifacts: dict[str, str] = {}
        for archive_path in [*(Path(item) for item in parent_archives), Path(archive)]:
            archive_manifest = verify_archive(archive_path)
            expected = {
                str(item["path"]): str(item["sha256"])
                for item in archive_manifest.get("artifact_entries", [])
            }
            with tarfile.open(archive_path, "r:gz") as incoming:
                _restore_artifacts(incoming, data_root, expected, predecessor_entries=previous_artifacts)
            previous_artifacts.update(expected)

    for namespace, rows in grouped.items():
        writer = _event_writer(target_specs[namespace].engine.meta_sqlite)
        for event in rows:
            writer(event)
    replayed = 0
    for namespace, rows in grouped.items():
        spec = target_specs[namespace]
        replay = getattr(spec.engine, "replay_repair_namespace", None)
        if not callable(replay):
            raise ArchiveError(f"target engine {type(spec.engine).__name__} lacks event replay")
        with _temporary_namespace(spec.engine, namespace):
            replay(namespace=namespace, from_seq=1, to_seq=rows[-1].seq, apply_indexes=False)
        replayed += 1
    # Portable replay always regenerates derived vector/index state.  Workspace
    # remapping is a separate concern from whether those projections are rebuilt.
    return RestoreReport(manifest["archive_id"], source_workspace, target_workspace, False, len(events), replayed, True)


def restore_backend_snapshot(
    *,
    archive: str | Path,
    target_data_dir: str | Path,
    backend: str,
    embedding_fingerprint: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Validate, and optionally extract, an exact compatible local snapshot."""
    manifest = verify_archive(archive)
    if manifest.get("archive_kind") != "base":
        raise ArchiveError("fast backend snapshot restore requires a base archive")
    if manifest.get("backend") not in {None, backend}:
        raise ArchiveError(f"snapshot backend {manifest.get('backend')!r} does not match {backend!r}")
    expected = str(manifest.get("embedding_fingerprint") or "")
    if not embedding_fingerprint or embedding_fingerprint != expected:
        raise ArchiveError("exact embedding_fingerprint is required to restore a backend snapshot")
    entries = list(manifest.get("backend_snapshot_entries") or [])
    if not entries:
        raise ArchiveError("archive does not contain a backend snapshot")
    if int(manifest.get("archive_format_version", 1)) < 2:
        raise ArchiveError("legacy snapshots without per-file checksums are not eligible for fast restore")
    target = Path(target_data_dir).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise ArchiveError(f"snapshot restore target is not empty: {target}")
    artifact_expected = {
        str(item["path"]): str(item["sha256"])
        for item in manifest.get("artifact_entries", [])
    }
    if not apply:
        return {
            "archive_id": manifest["archive_id"],
            "target_data_dir": str(target),
            "backend": backend,
            "embedding_fingerprint": expected,
            "snapshot_entries": len(entries),
            "dry_run": True,
        }
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as incoming:
        for entry in entries:
            relative = str(entry.get("relative_path") or entry.get("label") or "")
            if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ArchiveError("archive contains an unsafe backend snapshot path")
            prefix = str(Path("backend_snapshots") / relative).replace("\\", "/").rstrip("/") + "/"
            members = [
                member for member in incoming.getmembers()
                if member.name.startswith(prefix)
            ]
            if not members:
                raise ArchiveError(f"snapshot path is missing from archive: {relative!r}")
            for member in members:
                if member.issym() or member.islnk():
                    raise ArchiveError(f"archive contains an unsafe snapshot link: {member.name!r}")
                if not member.isfile():
                    continue
                member_relative = _safe_relative_member(member.name, "backend_snapshots")
                destination = target / member_relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = incoming.extractfile(member)
                if source is None:
                    raise ArchiveError(f"snapshot member is unreadable: {member.name!r}")
                destination.write_bytes(source.read())
        _restore_artifacts(incoming, target, artifact_expected)
    return {
        "archive_id": manifest["archive_id"],
        "target_data_dir": str(target),
        "backend": backend,
        "embedding_fingerprint": str(manifest.get("embedding_fingerprint") or ""),
        "snapshot_entries": len(entries),
        "dry_run": False,
    }

