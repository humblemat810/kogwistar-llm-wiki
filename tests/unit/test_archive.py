from __future__ import annotations

import json
import io
import tarfile

import pytest

from kogwistar.engine_core import EntityEventEnvelope
from kogwistar.engine_core.in_memory_meta import InMemoryMetaStore
from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar_llm_wiki.archive import (
    ArchiveError,
    create_archive,
    inspect_archive,
    restore_archive,
    restore_backend_snapshot,
    verify_archive,
)
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _node(workspace_id: str) -> Node:
    return Node(
        label="Archive node",
        type="entity",
        summary="Archive node",
        doc_id="doc-archive",
        mentions=[Grounding(spans=[Span.model_validate({
            "collection_page_url": "document_collection/doc-archive",
            "document_page_url": "document/doc-archive",
            "doc_id": "doc-archive", "insertion_method": "manual", "page_number": 1,
            "start_char": 0, "end_char": 1, "excerpt": "A",
            "context_before": "", "context_after": "",
        })])],
        metadata={"workspace_id": workspace_id, "graph_space": "source"},
    )


def _add_node(engines, workspace_id: str) -> None:
    namespace = WorkspaceNamespaces(workspace_id).source_space
    with _temporary_namespace(engines.kg, namespace):
        engines.kg.write.add_node(_node(workspace_id))


def test_event_envelope_preserves_identity_and_is_idempotent(tmp_path):
    engines = build_in_memory_namespace_engines()
    meta = engines.kg.meta_sqlite
    event = EntityEventEnvelope(
        namespace="ws:test:g:source", seq=1, event_id="archive-event-1",
        entity_kind="opaque", entity_id="id-1", op="ADD",
        payload_json=json.dumps({"value": 1}, separators=(",", ":")), created_at=123,
    )
    assert meta.append_entity_event_envelope(event) == 1
    assert meta.append_entity_event_envelope(event) == 1
    assert list(meta.iter_entity_event_envelopes(namespace=event.namespace)) == [event]
    with pytest.raises(ValueError, match="conflicts"):
        meta.append_entity_event_envelope(EntityEventEnvelope(
            namespace=event.namespace, seq=1, event_id=event.event_id,
            entity_kind=event.entity_kind, entity_id=event.entity_id, op=event.op,
            payload_json=json.dumps({"value": 2}, separators=(",", ":")), created_at=123,
        ))
    engines.close()


def test_archive_round_trip_remaps_workspace_and_replays_valid_node(tmp_path):
    source = build_in_memory_namespace_engines()
    _add_node(source, "source-ws")
    archive_path = tmp_path / "base.tar.gz"
    manifest = create_archive(source, workspace_id="source-ws", output=archive_path)
    assert manifest["archive_kind"] == "base"
    assert inspect_archive(archive_path)["event_count"] >= 1

    target = build_in_memory_namespace_engines()
    report = restore_archive(
        target, archive=archive_path, target_workspace_id="target-ws", apply=True
    )
    assert report.rebuilt_vectors is True
    assert report.imported_events >= 1
    namespace = WorkspaceNamespaces("target-ws").source_space
    with _temporary_namespace(target.kg, namespace):
        nodes = target.kg.read.get_nodes(limit=10)
    assert [node.label for node in nodes] == ["Archive node"]
    assert nodes[0].metadata["workspace_id"] == "target-ws"
    source.close()
    target.close()


def test_archive_round_trip_exact_restore_also_rebuilds_derived_vectors(tmp_path):
    source = build_in_memory_namespace_engines()
    _add_node(source, "ws")
    archive_path = tmp_path / "base.tar.gz"
    create_archive(source, workspace_id="ws", output=archive_path)

    target = build_in_memory_namespace_engines()
    report = restore_archive(target, archive=archive_path, apply=True)

    assert report.target_workspace_id == "ws"
    assert report.rebuilt_vectors is True
    source.close()
    target.close()


def test_incremental_archive_contains_only_events_after_parent_watermark(tmp_path):
    source = build_in_memory_namespace_engines()
    _add_node(source, "ws")
    base_path = tmp_path / "base.tar.gz"
    base = create_archive(source, workspace_id="ws", output=base_path)
    source.kg.meta_sqlite.append_entity_event(
        namespace=WorkspaceNamespaces("ws").source_space,
        event_id="later", entity_kind="opaque", entity_id="later", op="ADD",
        payload_json="{}",
    )
    delta_path = tmp_path / "delta.tar.gz"
    delta = create_archive(
        source, workspace_id="ws", output=delta_path, parent_archive=base_path
    )
    assert delta["archive_kind"] == "incremental"
    assert delta["event_count"] == 1
    assert verify_archive(delta_path)["parent_archive_id"] == base["archive_id"]
    source.close()


def test_archive_rejects_tampering_and_nonempty_restore_target(tmp_path):
    source = build_in_memory_namespace_engines()
    _add_node(source, "ws")
    archive_path = tmp_path / "base.tar.gz"
    create_archive(source, workspace_id="ws", output=archive_path)
    target = build_in_memory_namespace_engines()
    restore_archive(target, archive=archive_path, target_workspace_id="ws", apply=True)
    with pytest.raises(ArchiveError, match="not empty"):
        restore_archive(target, archive=archive_path, target_workspace_id="ws", apply=True)

    tampered_path = tmp_path / "tampered.tar.gz"
    with tarfile.open(archive_path, "r:gz") as incoming, tarfile.open(tampered_path, "w:gz") as outgoing:
        for member in incoming.getmembers():
            data = incoming.extractfile(member).read() if member.isfile() else None
            if member.name == "events.jsonl" and data is not None:
                data = data.replace(b"Archive node", b"Tampered node")
                member.size = len(data)
            outgoing.addfile(member, io.BytesIO(data) if data is not None else None)
    with pytest.raises((ArchiveError, OSError, EOFError)):
        verify_archive(tampered_path)
    source.close()
    target.close()


def test_in_memory_envelope_iteration_is_not_limited_to_one_batch():
    meta = InMemoryMetaStore()
    for seq in range(1, 506):
        meta.append_entity_event_envelope(EntityEventEnvelope(
            namespace="bulk", seq=seq, event_id=f"event-{seq}",
            entity_kind="opaque", entity_id=str(seq), op="ADD",
            payload_json="{}", created_at=seq,
        ))
    events = list(meta.iter_entity_event_envelopes(namespace="bulk", batch_size=500))
    assert len(events) == 505
    assert events[-1].seq == 505


def test_archive_restores_canonical_artifacts_without_overwriting_conflicts(tmp_path):
    source_data = tmp_path / "source-data"
    (source_data / "raw_documents").mkdir(parents=True)
    (source_data / "raw_documents" / "doc.md").write_text("source revision", encoding="utf-8")
    source = build_in_memory_namespace_engines(base_dir=tmp_path / "source-engines")
    _add_node(source, "ws")
    archive_path = tmp_path / "with-artifact.tar.gz"
    create_archive(source, workspace_id="ws", output=archive_path, data_dir=source_data)

    target_data = tmp_path / "target-data"
    target = build_in_memory_namespace_engines(base_dir=tmp_path / "target-engines")
    restore_archive(
        target,
        archive=archive_path,
        target_workspace_id="ws-copy",
        target_data_dir=target_data,
        apply=True,
    )
    assert (target_data / "raw_documents" / "doc.md").read_text(encoding="utf-8") == "source revision"
    (target_data / "raw_documents" / "doc.md").write_text("different", encoding="utf-8")
    with pytest.raises(ArchiveError, match="already differs"):
        restore_archive(
            build_in_memory_namespace_engines(base_dir=tmp_path / "target-engines-2"),
            archive=archive_path,
            target_workspace_id="ws-copy-2",
            target_data_dir=target_data,
            apply=True,
        )
    source.close()
    target.close()


def test_fast_backend_snapshot_requires_fingerprint_and_copies_nested_files(tmp_path):
    source = build_in_memory_namespace_engines(base_dir=tmp_path / "snapshot-source")
    archive_path = tmp_path / "snapshot.tar.gz"
    manifest = create_archive(
        source,
        workspace_id="ws",
        output=archive_path,
        data_dir=tmp_path / "snapshot-source",
        include_backend_snapshot=True,
        backend="chroma",
    )
    with pytest.raises(ArchiveError, match="embedding_fingerprint"):
        restore_backend_snapshot(
            archive=archive_path,
            target_data_dir=tmp_path / "snapshot-target",
            backend="chroma",
        )
    result = restore_backend_snapshot(
        archive=archive_path,
        target_data_dir=tmp_path / "snapshot-target",
        backend="chroma",
        embedding_fingerprint=manifest["embedding_fingerprint"],
    )
    assert result["snapshot_entries"] >= 1
    assert any((tmp_path / "snapshot-target").rglob("*"))
    source.close()
