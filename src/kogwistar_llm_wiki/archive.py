"""Operator-only portable event archives for LLM-Wiki workspaces.

The event log is authoritative. Backend files, vector collections, indexes,
reports, and Obsidian output are optional accelerators or sinks and are never
required to establish graph truth during restore.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from kogwistar.engine_core.event_envelope import EntityEventEnvelope

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace


ARCHIVE_FORMAT_VERSION = 1
_ARTIFACT_DIRS = ("raw_documents", "source_artifacts", "parser_runs", "workflow_runs", "maintenance")
_SECRET_NAMES = {".env", ".env.local", "auth.sqlite", "credentials.json", "secrets.json"}


class ArchiveError(RuntimeError):
    """Raised when an archive cannot be safely created, verified, or restored."""


@dataclass(frozen=True, slots=True)
class ArchiveNamespace:
    label: str
    engine: Any
    namespace: str


@dataclass(frozen=True, slots=True)
class RestoreReport:
    archive_id: str
    workspace_id: str
    target_workspace_id: str
    dry_run: bool
    imported_events: int
    replayed_namespaces: int
    rebuilt_vectors: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive_id": self.archive_id,
            "workspace_id": self.workspace_id,
            "target_workspace_id": self.target_workspace_id,
            "dry_run": self.dry_run,
            "imported_events": self.imported_events,
            "replayed_namespaces": self.replayed_namespaces,
            "rebuilt_vectors": self.rebuilt_vectors,
        }


def workspace_archive_namespaces(engines: NamespaceEngines, workspace_id: str) -> tuple[ArchiveNamespace, ...]:
    """Return every app-owned namespace and its authoritative metadata engine."""
    ns = WorkspaceNamespaces(workspace_id)
    values: list[ArchiveNamespace] = [
        ArchiveNamespace("conversation_fg", engines.conversation, ns.conv_fg),
        ArchiveNamespace("conversation_bg", engines.conversation, ns.conv_bg),
        ArchiveNamespace("workbench_jobs", engines.conversation, ns.workbench_jobs),
        ArchiveNamespace("source", engines.kg, ns.source_space),
        ArchiveNamespace("base_kg", engines.kg, ns.base_kg_space),
        ArchiveNamespace("curated_kg", engines.kg, ns.curated_kg_space),
        ArchiveNamespace("projection", engines.kg, ns.projection_space),
        ArchiveNamespace("projection_state", engines.kg, ns.projection_state),
        ArchiveNamespace("projection_manifest", engines.kg, ns.projection_manifest),
        ArchiveNamespace("usage_events", engines.kg, ns.usage_events),
        ArchiveNamespace("usage_projection", engines.kg, ns.usage_projection),
        ArchiveNamespace("workflow", engines.workflow, ns.workflow_space),
        ArchiveNamespace("workflow_maintenance", engines.workflow, ns.workflow_maintenance),
        ArchiveNamespace("review", engines.workflow, ns.review_space),
        ArchiveNamespace("maintenance_jobs", engines.workflow, ns.maintenance_jobs),
        ArchiveNamespace("projection_jobs", engines.workflow, ns.projection_jobs),
        ArchiveNamespace("wisdom", engines.wisdom, ns.wisdom_space),
        ArchiveNamespace("policy", engines.wisdom, ns.policy_space),
    ]
    if engines.derived_knowledge is not None:
        values.append(ArchiveNamespace("derived_knowledge", engines.derived_knowledge, ns.derived_knowledge))
    return tuple(values)


def _event_reader(meta: Any) -> Any:
    reader = getattr(meta, "iter_entity_event_envelopes", None)
    if not callable(reader):
        raise ArchiveError(
            f"metadata store {type(meta).__name__} lacks lossless event-envelope iteration"
        )
    return reader


def _event_writer(meta: Any) -> Any:
    writer = getattr(meta, "append_entity_event_envelope", None)
    if not callable(writer):
        raise ArchiveError(
            f"metadata store {type(meta).__name__} lacks lossless event-envelope import"
        )
    return writer


def _known_queue_activity(engines: NamespaceEngines) -> list[str]:
    activity: list[str] = []
    seen: set[int] = set()
    for item in (engines.conversation, engines.workflow, engines.kg, engines.wisdom, engines.derived_knowledge):
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        meta = getattr(item, "meta_sqlite", None)
        list_jobs = getattr(meta, "list_index_jobs", None)
        if not callable(list_jobs):
            continue
        for status in ("PENDING", "DOING"):
            for job in list_jobs(status=status, namespace=None, limit=1000):
                activity.append(f"{getattr(job, 'job_id', 'unknown')}:{status}")
    return activity


def assert_quiescent(engines: NamespaceEngines) -> None:
    """Fail closed when known durable index work is still active."""
    activity = _known_queue_activity(engines)
    if activity:
        raise ArchiveError(
            "archive requires quiescent writers; active durable work: "
            + ", ".join(activity[:20])
        )


def _canonical_event_line(event: EntityEventEnvelope) -> bytes:
    return (json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _embedding_profiles(engines: NamespaceEngines) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    seen: set[int] = set()
    for label, engine in (
        ("conversation", engines.conversation), ("workflow", engines.workflow),
        ("knowledge", engines.kg), ("wisdom", engines.wisdom),
        ("derived_knowledge", engines.derived_knowledge),
    ):
        if engine is None or id(engine) in seen:
            continue
        seen.add(id(engine))
        provider = getattr(engine, "_ef", None)
        backend = getattr(engine, "backend", None)
        profiles[label] = {
            "provider_type": type(provider).__name__ if provider is not None else None,
            "model": str(provider.name()) if provider is not None and callable(getattr(provider, "name", None)) else None,
            "dimension": getattr(backend, "embedding_dim", None),
        }
    return profiles


def _embedding_fingerprint(profiles: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(profiles), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _read_manifest(path: str | Path) -> dict[str, Any]:
    with tarfile.open(path, "r:gz") as archive:
        member = archive.getmember("manifest.json")
        raw = archive.extractfile(member)
        if raw is None:
            raise ArchiveError("archive has no manifest.json")
        value = json.load(raw)
    if value.get("archive_format_version") != ARCHIVE_FORMAT_VERSION:
        raise ArchiveError(f"unsupported archive format: {value.get('archive_format_version')!r}")
    return value


def inspect_archive(path: str | Path) -> dict[str, Any]:
    """Read and structurally validate an archive without writing anything."""
    manifest = _read_manifest(path)
    verify_archive(path)
    return manifest


def verify_archive(path: str | Path) -> dict[str, Any]:
    manifest = _read_manifest(path)
    expected_count = 0
    digest = hashlib.sha256()
    with tarfile.open(path, "r:gz") as archive:
        try:
            raw = archive.extractfile("events.jsonl")
        except KeyError as exc:
            raise ArchiveError("archive has no events.jsonl") from exc
        if raw is None:
            raise ArchiveError("archive has no readable events.jsonl")
        previous: dict[str, int] = {}
        first: dict[str, int] = {}
        for line in raw:
            if not line.strip():
                continue
            digest.update(line)
            event = EntityEventEnvelope.from_mapping(json.loads(line))
            prior = previous.get(event.namespace, 0)
            if event.seq != prior + 1 and not (prior == 0 and event.seq >= 1):
                raise ArchiveError(f"non-contiguous events in {event.namespace!r}")
            previous[event.namespace] = event.seq
            first.setdefault(event.namespace, event.seq)
            expected_count += 1
    if expected_count != int(manifest.get("event_count", -1)):
        raise ArchiveError("archive event count does not match manifest")
    if digest.hexdigest() != str(manifest.get("events_sha256")):
        raise ArchiveError("archive event checksum does not match manifest")
    ranges = manifest.get("ranges") or {}
    watermarks = manifest.get("watermarks") or {}
    for namespace, spec in ranges.items():
        start = int(spec.get("from_seq", 1))
        end = int(spec.get("to_seq", 0))
        if end < start - 1:
            raise ArchiveError(f"invalid archive range for {namespace!r}")
        if int(watermarks.get(namespace, -1)) != end:
            raise ArchiveError(f"archive watermark does not match range for {namespace!r}")
        if end == start - 1:
            if namespace in first:
                raise ArchiveError(f"empty archive range contains events for {namespace!r}")
            continue
        if first.get(namespace) != start or previous.get(namespace) != end:
            raise ArchiveError(f"archive range does not match events for {namespace!r}")
    if set(first) - set(ranges):
        raise ArchiveError("events contain a namespace missing from the manifest ranges")
    return manifest


def _iter_archive_events(path: str | Path) -> Iterator[EntityEventEnvelope]:
    with tarfile.open(path, "r:gz") as archive:
        raw = archive.extractfile("events.jsonl")
        if raw is None:
            raise ArchiveError("archive has no events.jsonl")
        for line in raw:
            if line.strip():
                yield EntityEventEnvelope.from_mapping(json.loads(line))


def _load_chain(path: str | Path, parents: Iterable[str | Path]) -> tuple[dict[str, Any], list[EntityEventEnvelope]]:
    paths = [Path(item) for item in parents] + [Path(path)]
    manifests = [verify_archive(item) for item in paths]
    for index, manifest in enumerate(manifests):
        parent_id = manifest.get("parent_archive_id")
        if parent_id:
            if index == 0 or parent_id != manifests[index - 1].get("archive_id"):
                raise ArchiveError("incremental archive parent does not match supplied parent")
        elif index != 0:
            raise ArchiveError("archive chain contains a non-base archive after a parent")
        if str(manifest.get("workspace_id")) != str(manifests[0].get("workspace_id")):
            raise ArchiveError("archive chain workspace IDs do not match")
        if index:
            parent_watermarks = manifests[index - 1].get("watermarks") or {}
            for namespace, spec in (manifest.get("ranges") or {}).items():
                expected_start = int(parent_watermarks.get(namespace, 0)) + 1
                if int(spec.get("from_seq", 1)) != expected_start:
                    raise ArchiveError(f"archive chain range does not continue {namespace!r}")
    events: list[EntityEventEnvelope] = []
    prior_by_ns: dict[str, int] = {}
    for item_path, manifest in zip(paths, manifests):
        expected_from = manifest.get("ranges", {})
        for event in _iter_archive_events(item_path):
            prior = prior_by_ns.get(event.namespace, 0)
            start = int(expected_from.get(event.namespace, {}).get("from_seq", 1))
            if event.seq < start or event.seq != prior + 1:
                raise ArchiveError(f"archive chain has a gap or overlap in {event.namespace!r}")
            prior_by_ns[event.namespace] = event.seq
            events.append(event)
    return manifests[-1], events


def _artifact_files(data_dir: Path) -> Iterator[tuple[Path, str]]:
    for dirname in _ARTIFACT_DIRS:
        root = data_dir / dirname
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink() or path.name in _SECRET_NAMES:
                continue
            yield path, (Path("artifacts") / path.relative_to(data_dir)).as_posix()


def _safe_relative_member(name: str, prefix: str) -> Path:
    value = Path(name.replace("\\", "/"))
    if value.is_absolute() or ".." in value.parts:
        raise ArchiveError(f"archive contains an unsafe path: {name!r}")
    try:
        return value.relative_to(prefix)
    except ValueError as exc:
        raise ArchiveError(f"archive member is outside {prefix!r}: {name!r}") from exc


def _restore_artifacts(
    archive: tarfile.TarFile,
    target_data_dir: Path,
    expected_entries: Mapping[str, Any] | None = None,
) -> int:
    restored = 0
    seen: set[str] = set()
    for member in archive.getmembers():
        if not member.name.startswith("artifacts/") or not member.isfile() or member.issym() or member.islnk():
            continue
        relative = _safe_relative_member(member.name, "artifacts")
        destination = target_data_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise ArchiveError(f"archive artifact is unreadable: {member.name!r}")
        incoming = source.read()
        expected = (expected_entries or {}).get(member.name)
        if expected is not None and hashlib.sha256(incoming).hexdigest() != str(expected):
            raise ArchiveError(f"artifact checksum does not match manifest: {member.name!r}")
        seen.add(member.name)
        if destination.exists():
            if destination.read_bytes() != incoming:
                raise ArchiveError(f"artifact target already differs: {destination}")
            continue
        destination.write_bytes(incoming)
        restored += 1
    missing = set(expected_entries or {}) - seen
    if missing:
        raise ArchiveError(f"archive artifact is missing: {sorted(missing)[0]!r}")
    return restored


def _assert_target_empty(specs: Mapping[str, ArchiveNamespace]) -> None:
    """Reject metadata or graph state that could make restore lossy."""
    for namespace, spec in specs.items():
        meta = getattr(spec.engine, "meta_sqlite", None)
        reader = _event_reader(meta)
        if list(reader(namespace=namespace, from_seq=1, to_seq=1)):
            raise ArchiveError(f"restore target namespace {namespace!r} is not empty")
        read = getattr(spec.engine, "read", None)
        for method_name in ("get_nodes", "get_edges"):
            method = getattr(read, method_name, None)
            if not callable(method):
                continue
            with _temporary_namespace(spec.engine, namespace):
                if method(limit=1):
                    raise ArchiveError(
                        f"restore target backend contains existing {method_name[4:-1]} data"
                    )


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
                reader = _event_reader(getattr(spec.engine, "meta_sqlite"))
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
            snapshot_root = staging / "backend_snapshots"
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
                snapshot_entries.append({"label": spec.label, "path": arcname.as_posix(), "relative_path": relative})

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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        existing = list(_event_reader(getattr(target_specs[namespace].engine, "meta_sqlite"))(
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
        for archive_path in [*(Path(item) for item in parent_archives), Path(archive)]:
            archive_manifest = verify_archive(archive_path)
            expected = {
                str(item["path"]): str(item["sha256"])
                for item in archive_manifest.get("artifact_entries", [])
            }
            with tarfile.open(archive_path, "r:gz") as incoming:
                _restore_artifacts(incoming, data_root, expected)

    for namespace, rows in grouped.items():
        writer = _event_writer(getattr(target_specs[namespace].engine, "meta_sqlite"))
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
) -> dict[str, Any]:
    """Extract an exact compatible local snapshot into a fresh data directory."""
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
    target = Path(target_data_dir).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise ArchiveError(f"snapshot restore target is not empty: {target}")
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
        expected = {
            str(item["path"]): str(item["sha256"])
            for item in manifest.get("artifact_entries", [])
        }
        _restore_artifacts(incoming, target, expected)
    return {
        "archive_id": manifest["archive_id"],
        "target_data_dir": str(target),
        "backend": backend,
        "embedding_fingerprint": expected,
        "snapshot_entries": len(entries),
    }


__all__ = [
    "ARCHIVE_FORMAT_VERSION",
    "ArchiveError",
    "ArchiveNamespace",
    "RestoreReport",
    "assert_quiescent",
    "create_archive",
    "inspect_archive",
    "restore_archive",
    "restore_backend_snapshot",
    "verify_archive",
    "workspace_archive_namespaces",
]
