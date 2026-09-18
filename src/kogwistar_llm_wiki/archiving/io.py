"""Archive namespace, artifact, and backend-safety helpers."""

from __future__ import annotations

import hashlib
import tarfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ..configuration.workspace import WorkspaceNamespaces
from ..models import NamespaceEngines
from ..utils import _temporary_namespace
from .archive_contracts import ARTIFACT_DIRS as _ARTIFACT_DIRS
from .archive_contracts import SECRET_NAMES as _SECRET_NAMES
from .archive_contracts import ArchiveError, ArchiveNamespace
from .validation import event_reader as _event_reader


def workspace_archive_namespaces(
    engines: NamespaceEngines,
    workspace_id: str,
) -> tuple[ArchiveNamespace, ...]:
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


def event_writer(meta: Any) -> Any:
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


def embedding_profiles(engines: NamespaceEngines) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    seen: set[int] = set()
    for label, engine in (
        ("conversation", engines.conversation),
        ("workflow", engines.workflow),
        ("knowledge", engines.kg),
        ("wisdom", engines.wisdom),
        ("derived_knowledge", engines.derived_knowledge),
    ):
        if engine is None or id(engine) in seen:
            continue
        seen.add(id(engine))
        report = getattr(engine, "embedding_profile_report", None) or {}
        registered = report.get("registered") if isinstance(report, Mapping) else None
        if not isinstance(registered, Mapping) or not registered.get("fingerprint"):
            raise ArchiveError(
                f"archive requires a registered embedding profile for {label}; "
                "initialize the engine with the profile guard before capture"
            )
        profiles[label] = dict(registered)
    return profiles


def embedding_fingerprint(profiles: Mapping[str, Any]) -> str:
    import json

    return hashlib.sha256(
        json.dumps(dict(profiles), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def artifact_files(data_dir: Path) -> Iterator[tuple[Path, str]]:
    for dirname in _ARTIFACT_DIRS:
        root = data_dir / dirname
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink() or path.name in _SECRET_NAMES:
                continue
            yield path, (Path("artifacts") / path.relative_to(data_dir)).as_posix()


def safe_relative_member(name: str, prefix: str) -> Path:
    value = Path(name.replace("\\", "/"))
    if value.is_absolute() or ".." in value.parts:
        raise ArchiveError(f"archive contains an unsafe path: {name!r}")
    try:
        return value.relative_to(prefix)
    except ValueError as exc:
        raise ArchiveError(f"archive member is outside {prefix!r}: {name!r}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore_artifacts(
    archive: tarfile.TarFile,
    target_data_dir: Path,
    expected_entries: Mapping[str, Any] | None = None,
    predecessor_entries: Mapping[str, Any] | None = None,
) -> int:
    restored = 0
    seen: set[str] = set()
    for member in archive.getmembers():
        if not member.name.startswith("artifacts/") or not member.isfile() or member.issym() or member.islnk():
            continue
        relative = safe_relative_member(member.name, "artifacts")
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
                predecessor = (predecessor_entries or {}).get(member.name)
                if predecessor is None or sha256_file(destination) != str(predecessor):
                    raise ArchiveError(f"artifact target already differs: {destination}")
                destination.write_bytes(incoming)
                restored += 1
            continue
        destination.write_bytes(incoming)
        restored += 1
    missing = set(expected_entries or {}) - seen
    if missing:
        raise ArchiveError(f"archive artifact is missing: {min(missing)!r}")
    return restored


def assert_target_empty(specs: Mapping[str, ArchiveNamespace]) -> None:
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
