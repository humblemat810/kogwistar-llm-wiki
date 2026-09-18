from __future__ import annotations

import hashlib
import json
import tarfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from kogwistar.engine_core.event_envelope import EntityEventEnvelope

from .archive_contracts import READABLE_ARCHIVE_FORMATS, ArchiveError


def event_reader(meta: Any) -> Any:
    reader = getattr(meta, "iter_entity_event_envelopes", None)
    if not callable(reader):
        raise ArchiveError(f"metadata store {type(meta).__name__} lacks lossless event-envelope iteration")
    return reader


def canonical_event_line(event: EntityEventEnvelope) -> bytes:
    return (
        json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def read_manifest(path: str | Path) -> dict[str, Any]:
    with tarfile.open(path, "r:gz") as archive:
        member = archive.getmember("manifest.json")
        raw = archive.extractfile(member)
        if raw is None:
            raise ArchiveError("archive has no manifest.json")
        value = json.load(raw)
    if value.get("archive_format_version") not in READABLE_ARCHIVE_FORMATS:
        raise ArchiveError(f"unsupported archive format: {value.get('archive_format_version')!r}")
    return value


def verify_artifact_payloads(*, archive_path: str | Path, manifest: Mapping[str, Any]) -> None:
    """Verify every declared artifact and v2 snapshot file before restore."""
    version = int(manifest.get("archive_format_version", 1))
    artifacts: dict[str, str] = {}
    for item in manifest.get("artifact_entries", []):
        if not isinstance(item, Mapping) or not item.get("path") or not item.get("sha256"):
            if version >= 2:
                raise ArchiveError("v2 artifact entry has no checksum")
            continue
        path = str(item["path"])
        checksum = str(item["sha256"])
        if path in artifacts and artifacts[path] != checksum:
            raise ArchiveError(f"artifact has conflicting checksums: {path!r}")
        artifacts[path] = checksum
    snapshots = [item for item in manifest.get("backend_snapshot_entries", []) if isinstance(item, Mapping)]
    snapshot_files: dict[str, str] = {}
    for entry in snapshots:
        files = entry.get("files")
        if files is None:
            if manifest.get("archive_format_version", 1) >= 2:
                raise ArchiveError("v2 snapshot entry has no per-file checksums")
            continue
        for item in files:
            if not isinstance(item, Mapping) or not item.get("path") or not item.get("sha256"):
                raise ArchiveError("snapshot checksum entry is malformed")
            snapshot_files[str(item["path"])] = str(item["sha256"])
    with tarfile.open(archive_path, "r:gz") as archive:
        declared = {**artifacts, **snapshot_files}
        seen: set[str] = set()
        for member in archive.getmembers():
            if not member.isfile() or member.issym() or member.islnk():
                continue
            if not (member.name.startswith("artifacts/") or member.name.startswith("backend_snapshots/")):
                continue
            if member.name not in declared:
                continue
            raw = archive.extractfile(member)
            if raw is None:
                raise ArchiveError(f"archive payload is unreadable: {member.name!r}")
            actual = hashlib.sha256(raw.read()).hexdigest()
            if actual != declared[member.name]:
                raise ArchiveError(f"archive payload checksum does not match manifest: {member.name!r}")
            seen.add(member.name)
        missing = set(declared) - seen
        if missing:
            raise ArchiveError(f"archive payload is missing: {min(missing)!r}")


def verify_archive(path: str | Path) -> dict[str, Any]:
    manifest = read_manifest(path)
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
    verify_artifact_payloads(archive_path=path, manifest=manifest)
    return manifest


def inspect_archive(path: str | Path) -> dict[str, Any]:
    """Read and structurally validate an archive without writing anything."""
    manifest = read_manifest(path)
    verify_archive(path)
    return manifest


def iter_archive_events(path: str | Path) -> Iterator[EntityEventEnvelope]:
    with tarfile.open(path, "r:gz") as archive:
        raw = archive.extractfile("events.jsonl")
        if raw is None:
            raise ArchiveError("archive has no events.jsonl")
        for line in raw:
            if line.strip():
                yield EntityEventEnvelope.from_mapping(json.loads(line))


def load_chain(path: str | Path, parents: Iterable[str | Path]) -> tuple[dict[str, Any], list[EntityEventEnvelope]]:
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
        for event in iter_archive_events(item_path):
            prior = prior_by_ns.get(event.namespace, 0)
            start = int(expected_from.get(event.namespace, {}).get("from_seq", 1))
            if event.seq < start or event.seq != prior + 1:
                raise ArchiveError(f"archive chain has a gap or overlap in {event.namespace!r}")
            prior_by_ns[event.namespace] = event.seq
            events.append(event)
    return manifests[-1], events
