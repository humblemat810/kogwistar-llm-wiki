from __future__ import annotations

import json

import pytest

from kogwistar.runtime import BudgetAttribution, BudgetEvent
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.usage_projection import UsageProjection, append_usage_event, persist_usage_events


def _projection(tmp_path):
    engines = build_in_memory_namespace_engines(tmp_path / "engines")
    namespaces = WorkspaceNamespaces("ws-1")
    return engines.conversation.meta_sqlite, namespaces, UsageProjection(
        engines.conversation.meta_sqlite,
        workspace_id="ws-1",
        source_namespace=namespaces.usage_events,
        projection_namespace=namespaces.usage_projection,
    )


def test_usage_projection_materializes_dimensions_and_checkpoint(tmp_path):
    meta, namespaces, projection = _projection(tmp_path)
    attribution = BudgetAttribution(
        workspace_id="ws-1",
        source_document_id="doc-1",
        operation_id="op-1",
        operation_kind="parse",
        maintenance_job_id="job-1",
        dream_job_id="dream-1",
        provider="azure",
        model="gpt-5-mini",
    )
    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-1",
            run_id="run-1",
            source="provider",
            kind="token",
            amount=12,
            unit="input_tokens",
            ts_ms=100,
            attribution=attribution,
        ),
    )
    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-2",
            run_id="run-1",
            source="provider",
            kind="cost",
            amount=0.25,
            unit="total_cost",
            ts_ms=110,
            attribution=attribution,
        ),
    )

    snapshot = projection.refresh()

    assert snapshot.materialization_status == "materialized"
    assert snapshot.last_materialized_seq == snapshot.last_authoritative_seq == 2
    assert snapshot.projected_at_ms is not None
    assert snapshot.last_source_event_ts_ms == 110
    assert snapshot.snapshot_id
    assert snapshot.aggregates["document"]["doc-1"]["input_tokens"] == 12
    assert snapshot.aggregates["operation"]["op-1"]["total_cost"] == 0.25
    assert snapshot.aggregates["maintenance_job"]["job-1"]["event_count"] == 2
    assert snapshot.aggregates["dream_job"]["dream-1"]["event_count"] == 2
    assert snapshot.aggregates["run"]["run-1"]["total_cost"] == 0.25


def test_usage_projection_resumes_from_watermark_and_is_idempotent(tmp_path):
    meta, namespaces, projection = _projection(tmp_path)
    event = BudgetEvent(
        event_id="event-1",
        run_id="run-1",
        source="provider",
        kind="token",
        amount=7,
        unit="output_tokens",
        attribution=BudgetAttribution(source_document_id="doc-1"),
    )
    append_usage_event(meta, namespace=namespaces.usage_events, event=event)
    first = projection.refresh()

    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-2",
            run_id="run-2",
            source="runtime",
            kind="debit",
            amount=3,
            unit="ms",
        ),
    )
    second = projection.refresh()
    repeated = projection.refresh()

    assert first.last_materialized_seq == 1
    assert second.source_from_seq == 2
    assert second.last_materialized_seq == 2
    assert second.aggregates["document"]["doc-1"]["output_tokens"] == 7
    assert second.aggregates["unattributed"]["unattributed"]["time_ms"] == 3
    assert repeated.raw_event_count == second.raw_event_count == 2
    assert repeated.aggregates == second.aggregates


def test_usage_projection_distinguishes_unknown_cost_from_zero_cost(tmp_path):
    meta, namespaces, projection = _projection(tmp_path)
    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-token-only",
            run_id="run-1",
            source="provider",
            kind="token",
            amount=2,
            unit="input_tokens",
        ),
    )

    unknown = projection.refresh()
    unknown_aggregate = unknown.aggregates["unattributed"]["unattributed"]
    assert unknown_aggregate["total_cost"] is None
    assert unknown_aggregate["cost_observed"] is False

    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-zero-cost",
            run_id="run-1",
            source="provider",
            kind="cost",
            amount=0,
            unit="total_cost",
        ),
    )
    known = projection.refresh().aggregates["unattributed"]["unattributed"]
    assert known["total_cost"] == 0.0
    assert known["cost_observed"] is True


def test_usage_projection_defers_events_after_captured_watermark(tmp_path, monkeypatch):
    meta, namespaces, projection = _projection(tmp_path)
    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-1",
            run_id="run-1",
            source="provider",
            kind="token",
            amount=1,
            unit="input_tokens",
        ),
    )
    original_latest = meta.get_latest_entity_event_seq
    calls = 0

    def latest_with_late_event(*, namespace: str = "default") -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            append_usage_event(
                meta,
                namespace=namespaces.usage_events,
                event=BudgetEvent(
                    event_id="event-2",
                    run_id="run-2",
                    source="provider",
                    kind="cost",
                    amount=0.5,
                    unit="total_cost",
                    ),
                )
        return original_latest(namespace=namespace)

    monkeypatch.setattr(meta, "get_latest_entity_event_seq", latest_with_late_event)
    snapshot = projection.refresh()

    assert snapshot.last_materialized_seq == 1
    assert snapshot.materialization_status == "catching_up"
    assert snapshot.aggregates["run"]["run-1"]["input_tokens"] == 1


def test_usage_projection_rebuilds_when_schema_is_incompatible(tmp_path):
    meta, namespaces, projection = _projection(tmp_path)
    meta.replace_named_projection(
        namespaces.usage_projection,
        "ws-1",
        {"projection_schema_version": 999},
        last_authoritative_seq=8,
        last_materialized_seq=8,
        projection_schema_version=999,
        materialization_status="materialized",
    )
    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-1",
            run_id="run-1",
            source="provider",
            kind="token",
            amount=2,
            unit="input_tokens",
        ),
    )

    snapshot = projection.refresh()

    assert snapshot.rebuild_reason == "incompatible_projection"
    assert snapshot.last_materialized_seq == 1
    assert snapshot.aggregates["unattributed"]["unattributed"]["input_tokens"] == 2


def test_usage_projection_snapshot_rejects_incompatible_identity(tmp_path):
    meta, namespaces, projection = _projection(tmp_path)
    meta.replace_named_projection(
        namespaces.usage_projection,
        "ws-1",
        {
            "projection_id": "usage",
            "workspace_id": "other-workspace",
            "projection_schema_version": 1,
            "source_namespace": namespaces.usage_events,
        },
        last_authoritative_seq=0,
        last_materialized_seq=0,
        projection_schema_version=1,
        materialization_status="materialized",
    )

    with pytest.raises(ValueError, match="workspace"):
        projection.snapshot()


def test_usage_projection_failure_preserves_last_checkpoint(tmp_path, monkeypatch):
    meta, namespaces, projection = _projection(tmp_path)
    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-1",
            run_id="run-1",
            source="provider",
            kind="token",
            amount=2,
            unit="input_tokens",
        ),
    )
    previous = projection.refresh()

    append_usage_event(
        meta,
        namespace=namespaces.usage_events,
        event=BudgetEvent(
            event_id="event-2",
            run_id="run-2",
            source="provider",
            kind="token",
            amount=4,
            unit="output_tokens",
        ),
    )

    def fail_iter(**kwargs):
        raise RuntimeError("source read failed")

    monkeypatch.setattr(meta, "iter_entity_events", fail_iter)
    with pytest.raises(RuntimeError, match="source read failed"):
        projection.refresh()

    failed = projection.snapshot()
    assert failed is not None
    assert failed.materialization_status == "failed"
    assert failed.last_materialized_seq == previous.last_materialized_seq == 1
    assert failed.raw_event_count == previous.raw_event_count == 1

    monkeypatch.undo()
    recovered = projection.refresh()
    assert recovered.materialization_status == "materialized"
    assert recovered.last_materialized_seq == 2
    assert recovered.aggregates["unattributed"]["unattributed"]["output_tokens"] == 4


def test_usage_event_serialization_preserves_attribution():
    from kogwistar.runtime import budget_event_from_dict, budget_event_to_dict

    event = BudgetEvent(
        event_id="event-1",
        run_id="run-1",
        source="provider",
        kind="cost",
        amount=0.2,
        unit="total_cost",
        attribution=BudgetAttribution(operation_id="op-1", model="gpt-5-mini"),
    )

    restored = budget_event_from_dict(json.loads(json.dumps(budget_event_to_dict(event))))

    assert restored == event


def test_persist_usage_events_scopes_ids_to_attempt_and_host_attribution(tmp_path):
    meta, namespaces, _projection_instance = _projection(tmp_path)
    event = BudgetEvent(
        run_id="runtime-run",
        source="runtime",
        kind="token",
        amount=4,
        unit="input_tokens",
    )

    persist_usage_events(
        meta,
        namespace=namespaces.usage_events,
        events=[event],
        workspace_id="ws-1",
        attempt_id="attempt-1",
        source_document_id="doc-1",
        operation_kind="parser",
    )
    persist_usage_events(
        meta,
        namespace=namespaces.usage_events,
        events=[event],
        workspace_id="ws-1",
        attempt_id="attempt-2",
        source_document_id="doc-1",
        operation_kind="parser",
    )

    rows = list(meta.iter_entity_events(namespace=namespaces.usage_events, from_seq=1))
    assert len(rows) == 2
    payloads = [json.loads(row[4]) for row in rows]
    assert len({payload["event_id"] for payload in payloads}) == 2
    assert {payload["attribution"]["workspace_id"] for payload in payloads} == {"ws-1"}
    assert {payload["attribution"]["source_document_id"] for payload in payloads} == {"doc-1"}
