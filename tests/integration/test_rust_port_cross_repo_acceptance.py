from __future__ import annotations

import json
import difflib
from pathlib import Path

import pytest

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import (
    EmbeddingProviderConfig,
    ProviderEndpointConfig,
    WorkflowProviderSettings,
)
from kogwistar._rust_bridge import store_sqlite
from kogwistar.engine_core.engine_sqlite import EngineSQLite
from kogwistar_llm_wiki.namespaces import GraphSpace
from kogwistar_obsidian_sink.cdc.event_consumer import JsonlEventConsumer
from kogwistar_obsidian_sink.integrations.kogwistar_adapter import KogwistarDuckProvider
from kogwistar_obsidian_sink.sinks.obsidian import ObsidianVaultSink


pytestmark = [pytest.mark.ci_full, pytest.mark.integration]


def _install_fake_parser(pipeline) -> None:
    settings = WorkflowProviderSettings(
        parser=ProviderEndpointConfig(provider="fake", model="adr015-cross-repo"),
        embedding=EmbeddingProviderConfig(
            provider="fake",
            model="adr015-cross-repo-embed",
            dimension=2,
        ),
    )

    def parse(**kwargs):
        kwargs.pop("llm_provider", None)
        kwargs.pop("model", None)
        kwargs.pop("provider_settings", None)
        return parse_page_index_document(provider_settings=settings, **kwargs)

    pipeline.parser = parse


def _append(path: Path, *, event_id: str, entity: dict, op: str) -> None:
    payload = (
        {"reason": "ADR-015 cross-repository tombstone"}
        if op == "TOMBSTONE"
        else entity
    )
    result = store_sqlite(
        path=path,
        operation={
            "kind": "raw_append",
            "namespace": "adr015-cross-repo",
            "event_id": event_id,
            "entity_kind": "node",
            "entity_id": entity["id"],
            "op": op,
            "payload_json": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    )
    assert result["inserted"] is True


def _sink_event(row: dict) -> dict:
    if row["op"] in {"TOMBSTONE", "DELETE"}:
        event_type = "entity.tombstone"
        entity = {"id": row["entity_id"]}
    else:
        event_type = "entity.upsert"
        entity = json.loads(row["payload_json"])
    return {
        "type": event_type,
        "event_seq": row["seq"],
        "version": row["seq"],
        "entity": entity,
    }


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _projected_files(root: Path) -> dict[str, bytes]:
    included = {".md", ".canvas"}
    files = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix in included
    }
    state = root / "System" / "materialized_state.json"
    files["System/materialized_state.json"] = state.read_bytes()
    return files


def test_source_to_rust_events_incremental_vault_equals_full_rebuild(
    pipeline,
    ingest_request,
    tmp_path: Path,
) -> None:
    """ADR-015 release gate across parser, app, Rust store, and sink repos."""
    pytest.importorskip("kogwistar._rust")
    _install_fake_parser(pipeline)

    alpha = pipeline.run(
        ingest_request.model_copy(
            update={
                "promotion_mode": "sync",
                "source_uri": "file:///adr015/alpha.txt",
                "title": "ADR Alpha",
                "raw_text": "ADR Alpha records a stable contract.",
            }
        )
    )
    beta = pipeline.run(
        ingest_request.model_copy(
            update={
                "promotion_mode": "sync",
                "source_uri": "file:///adr015/beta.txt",
                "title": "ADR Beta",
                "raw_text": "ADR Beta records a replaceable contract.",
            }
        )
    )
    assert alpha.promoted_entity_id and beta.promoted_entity_id

    snapshot = pipeline.projection.build_projection_snapshot(
        ingest_request.workspace_id,
        graph_spaces=[GraphSpace.CURATED_KG],
    )
    by_id = {
        entity.kg_id: {
            "id": entity.kg_id,
            "label": entity.title,
            "type": entity.entity_type,
            "summary": entity.summary,
            "metadata": entity.metadata,
            "source_ids": entity.source_ids,
            "target_ids": entity.target_ids,
        }
        for entity in snapshot.entities
    }
    alpha_entity = by_id[alpha.promoted_entity_id]
    beta_entity = by_id[beta.promoted_entity_id]
    beta_replaced = {
        **beta_entity,
        "summary": "ADR Beta replacement is authoritative.",
    }

    python_store = EngineSQLite(tmp_path / "shared-store")
    python_store.ensure_initialized()
    db_path = python_store.db_path
    _append(db_path, event_id="alpha-add", entity=alpha_entity, op="ADD")
    _append(db_path, event_id="beta-add", entity=beta_entity, op="ADD")
    _append(db_path, event_id="beta-replace", entity=beta_replaced, op="REPLACE")
    _append(db_path, event_id="alpha-tombstone", entity=alpha_entity, op="TOMBSTONE")

    rows = store_sqlite(
        path=db_path,
        operation={
            "kind": "exclusive_raw_replay",
            "namespace": "adr015-cross-repo",
            "after_seq": 0,
            "limit": 100,
        },
    )
    assert [row["op"] for row in rows] == ["ADD", "ADD", "REPLACE", "TOMBSTONE"]
    assert [row[3] for row in python_store.iter_entity_events(
        namespace="adr015-cross-repo", from_seq=1
    )] == ["ADD", "ADD", "REPLACE", "TOMBSTONE"]

    incremental = tmp_path / "incremental-vault"
    consumer = JsonlEventConsumer(incremental)
    initial_path = tmp_path / "initial.jsonl"
    update_path = tmp_path / "update.jsonl"
    tombstone_path = tmp_path / "tombstone.jsonl"
    sink_events = [_sink_event(row) for row in rows]
    _write_events(initial_path, sink_events[:2])
    _write_events(update_path, sink_events[2:3])
    _write_events(tombstone_path, sink_events[3:])

    consumer.consume(initial_path)
    ledger = json.loads((incremental / "System" / "ledger.json").read_text(encoding="utf-8"))
    alpha_note = incremental / ledger["by_id"][alpha_entity["id"]]
    beta_note = incremental / ledger["by_id"][beta_entity["id"]]
    assert alpha_note.is_file() and beta_note.is_file()

    consumer.consume(update_path)
    assert "ADR Beta replacement is authoritative." in beta_note.read_text(encoding="utf-8")
    consumer.consume(tombstone_path)
    assert not alpha_note.exists()
    assert beta_note.is_file()

    recovered = store_sqlite(
        path=db_path,
        operation={
            "kind": "recover_entity_projection",
            "namespace": "adr015-cross-repo",
            "consumer": "obsidian",
            "projection_namespace": "adr015-projection",
            "projection_key": "vault",
            "batch_limit": 100,
        },
    )
    rebuilt = store_sqlite(
        path=db_path,
        operation={
            "kind": "rebuild_entity_projection",
            "namespace": "adr015-cross-repo",
            "consumer": "obsidian",
            "projection_namespace": "adr015-projection",
            "projection_key": "vault",
        },
    )
    assert recovered["canonical_payload"] == rebuilt["canonical_payload"]
    assert recovered["digest"] == rebuilt["digest"]

    reduced = json.loads(rebuilt["canonical_payload"])
    active_entities = [
        item["entity"]
        for item in reduced["entities"].values()
        if item["deleted"] is False
    ]
    assert [entity["id"] for entity in active_entities] == [beta_entity["id"]]

    full = tmp_path / "full-rebuild-vault"
    ObsidianVaultSink(full).build(
        KogwistarDuckProvider.from_entity_projection(rebuilt["canonical_payload"])
    )
    incremental_files = _projected_files(incremental)
    full_files = _projected_files(full)
    assert incremental_files.keys() == full_files.keys()
    for relative_path in incremental_files:
        assert incremental_files[relative_path] == full_files[relative_path], "".join(
            difflib.unified_diff(
                incremental_files[relative_path].decode("utf-8").splitlines(keepends=True),
                full_files[relative_path].decode("utf-8").splitlines(keepends=True),
                fromfile=f"incremental/{relative_path}",
                tofile=f"full/{relative_path}",
            )
        )
