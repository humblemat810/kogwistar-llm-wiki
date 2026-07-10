"""Incremental, checkpointed usage projections for llm-wiki."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from kogwistar.id_provider import stable_id
from kogwistar.runtime import (
    BudgetAttribution,
    BudgetEvent,
    ProjectionCheckpoint,
    ProjectionLoadResult,
    refresh_checkpointed_named_projection,
    budget_event_from_dict,
    budget_event_to_dict,
)
from kogwistar.runtime.budget_adapters import summarize_budget_events


USAGE_PROJECTION_SCHEMA_VERSION = 1
USAGE_EVENT_KIND = "usage_event"


class UsageMetaStore(Protocol):
    def append_entity_event(
        self,
        *,
        namespace: str = "default",
        event_id: str,
        entity_kind: str,
        entity_id: str,
        op: str,
        payload_json: str,
    ) -> int: ...

    def get_latest_entity_event_seq(self, *, namespace: str = "default") -> int: ...

    def iter_entity_events(
        self,
        *,
        namespace: str = "default",
        from_seq: int = 1,
        to_seq: int | None = None,
        batch_size: int = 500,
    ) -> Iterable[tuple[int, str, str, str, str]]: ...

    def get_named_projection(self, namespace: str, key: str) -> dict[str, Any] | None: ...

    def replace_named_projection(
        self,
        namespace: str,
        key: str,
        payload: dict[str, Any],
        *,
        last_authoritative_seq: int,
        last_materialized_seq: int,
        projection_schema_version: int,
        materialization_status: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _UsageProjectionState:
    aggregates: dict[str, dict[str, dict[str, object]]]


@dataclass(frozen=True, slots=True)
class UsageProjectionSnapshot:
    projection_id: str
    workspace_id: str
    projection_schema_version: int
    source_namespace: str
    source_from_seq: int
    source_to_seq: int
    last_authoritative_seq: int
    last_materialized_seq: int
    projected_at_ms: int | None
    last_source_event_ts_ms: int | None
    snapshot_id: str
    raw_event_count: int
    materialization_status: str
    rebuild_reason: str | None
    aggregates: dict[str, dict[str, dict[str, object]]]

    def as_dict(self) -> dict[str, object]:
        return {
            "projection_id": self.projection_id,
            "workspace_id": self.workspace_id,
            "projection_schema_version": self.projection_schema_version,
            "source_namespace": self.source_namespace,
            "source_from_seq": self.source_from_seq,
            "source_to_seq": self.source_to_seq,
            "last_authoritative_seq": self.last_authoritative_seq,
            "last_materialized_seq": self.last_materialized_seq,
            "projected_at_ms": self.projected_at_ms,
            "last_source_event_ts_ms": self.last_source_event_ts_ms,
            "snapshot_id": self.snapshot_id,
            "raw_event_count": self.raw_event_count,
            "materialization_status": self.materialization_status,
            "rebuild_reason": self.rebuild_reason,
            "aggregates": self.aggregates,
        }


def _event_id(event: BudgetEvent) -> str:
    if event.event_id:
        return str(event.event_id)
    return str(
        stable_id(
            "usage_event",
            event.run_id,
            event.source,
            event.kind,
            event.amount,
            event.unit,
            event.scope,
            event.ts_ms,
            event.meta,
            event.attribution.as_dict() if event.attribution else {},
        )
    )


def append_usage_event(meta: UsageMetaStore, *, namespace: str, event: BudgetEvent) -> int:
    """Append one idempotently identifiable raw usage event to the core event log."""

    event_id = _event_id(event)
    payload = budget_event_to_dict(event)
    payload["event_id"] = event_id
    payload["artifact_kind"] = USAGE_EVENT_KIND
    return meta.append_entity_event(
        namespace=namespace,
        event_id=event_id,
        entity_kind=USAGE_EVENT_KIND,
        entity_id=event_id,
        op="ADD",
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def persist_usage_events(
    meta: UsageMetaStore,
    *,
    namespace: str,
    events: Iterable[BudgetEvent],
    workspace_id: str,
    attempt_id: str,
    source_document_id: str | None = None,
    operation_id: str | None = None,
    operation_kind: str | None = None,
    maintenance_job_id: str | None = None,
    dream_job_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Persist one runtime attempt's events with host-owned attribution."""

    for index, event in enumerate(events):
        existing = event.attribution or BudgetAttribution()
        effective_operation_id = existing.operation_id or operation_id or str(
            stable_id("kogwistar_llm_wiki.operation", attempt_id, index)
        )
        attribution = replace(
            existing,
            workspace_id=workspace_id,
            source_document_id=source_document_id if source_document_id is not None else existing.source_document_id,
            operation_id=effective_operation_id,
            operation_kind=operation_kind or existing.operation_kind,
            maintenance_job_id=maintenance_job_id or existing.maintenance_job_id,
            dream_job_id=dream_job_id or existing.dream_job_id,
            provider=provider or existing.provider,
            model=model or existing.model,
        )
        enriched = replace(
            event,
            event_id=str(
                stable_id(
                    "kogwistar_llm_wiki.usage_event",
                    workspace_id,
                    attempt_id,
                    index,
                    event.event_id,
                )
            ),
            attribution=attribution,
        )
        append_usage_event(meta, namespace=namespace, event=enriched)


def _empty_aggregate() -> dict[str, object]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "total_cost": None,
        "cost_observed": False,
        "time_ms": 0,
        "event_count": 0,
        "event_counts": {},
        "by_unit": {},
        "providers": [],
        "models": [],
    }


def _merge_event(aggregate: dict[str, object], event: BudgetEvent) -> None:
    summary = summarize_budget_events([event])
    for key in ("input_tokens", "output_tokens", "total_tokens", "time_ms", "event_count"):
        aggregate[key] = int(aggregate.get(key, 0) or 0) + int(summary.get(key, 0) or 0)
    cost_observed = bool(aggregate.get("cost_observed")) or event.kind == "cost" or event.unit == "total_cost"
    aggregate["cost_observed"] = cost_observed
    aggregate["total_cost"] = (
        round(
            float(aggregate.get("total_cost", 0.0) or 0.0)
            + float(summary.get("total_cost", 0.0) or 0.0),
            6,
        )
        if cost_observed
        else None
    )
    for field in ("event_counts", "by_unit"):
        target = dict(aggregate.get(field) or {})
        for key, value in dict(summary.get(field) or {}).items():
            target[str(key)] = int(target.get(str(key), 0) or 0) + int(value or 0)
        aggregate[field] = target
    attribution = event.attribution
    if attribution is not None:
        if attribution.provider and attribution.provider not in aggregate["providers"]:
            aggregate["providers"] = sorted([*aggregate["providers"], attribution.provider])
        if attribution.model and attribution.model not in aggregate["models"]:
            aggregate["models"] = sorted([*aggregate["models"], attribution.model])


def _event_groups(event: BudgetEvent) -> dict[str, list[str]]:
    attribution = event.attribution
    groups: dict[str, list[str]] = {"run": [event.run_id] if event.run_id else []}
    domain_keys: list[str] = []
    if attribution is not None:
        groups.update(
            {
                "document": [attribution.source_document_id] if attribution.source_document_id else [],
                "operation": [attribution.operation_id] if attribution.operation_id else [],
                "maintenance_job": [attribution.maintenance_job_id] if attribution.maintenance_job_id else [],
                "dream_job": [attribution.dream_job_id] if attribution.dream_job_id else [],
            }
        )
        domain_keys = [
            key
            for dimension in ("document", "operation", "maintenance_job", "dream_job")
            for key in groups.get(dimension, [])
        ]
    if not domain_keys:
        groups["unattributed"] = ["unattributed"]
    return {dimension: keys for dimension, keys in groups.items() if keys}


def _decode_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("usage projection payload must be an object")
    if int(row.get("projection_schema_version") or 0) != USAGE_PROJECTION_SCHEMA_VERSION:
        raise ValueError("usage projection schema version is incompatible")
    if int(payload.get("projection_schema_version") or 0) != USAGE_PROJECTION_SCHEMA_VERSION:
        raise ValueError("usage projection payload schema version is incompatible")
    return payload


class UsageProjection:
    """Build and resume a workspace usage projection from raw core events."""

    def __init__(
        self,
        meta: UsageMetaStore,
        *,
        workspace_id: str,
        source_namespace: str,
        projection_namespace: str,
        projection_id: str = "usage",
    ) -> None:
        self.meta = meta
        self.workspace_id = workspace_id
        self.source_namespace = source_namespace
        self.projection_namespace = projection_namespace
        self.projection_id = projection_id

    def _projection_checkpoint_from_row(self, row: Mapping[str, Any]) -> ProjectionCheckpoint:
        payload = _decode_projection(row)
        if str(payload.get("projection_id")) != self.projection_id:
            raise ValueError("usage projection id is incompatible")
        if str(payload.get("workspace_id")) != self.workspace_id:
            raise ValueError("usage projection workspace is incompatible")
        if str(payload.get("source_namespace")) != self.source_namespace:
            raise ValueError("usage projection source namespace is incompatible")
        return ProjectionCheckpoint(
            projection_id=str(payload.get("projection_id") or self.projection_id),
            workspace_id=str(payload.get("workspace_id") or self.workspace_id),
            projection_schema_version=int(payload.get("projection_schema_version") or USAGE_PROJECTION_SCHEMA_VERSION),
            source_namespace=str(payload.get("source_namespace") or self.source_namespace),
            source_from_seq=int(payload.get("source_from_seq") or 0),
            source_to_seq=int(payload.get("source_to_seq") or 0),
            last_authoritative_seq=int(row.get("last_authoritative_seq") or 0),
            last_materialized_seq=int(row.get("last_materialized_seq") or 0),
            projected_at_ms=int(payload["projected_at_ms"]) if payload.get("projected_at_ms") is not None else None,
            last_source_event_ts_ms=(
                int(payload["last_source_event_ts_ms"])
                if payload.get("last_source_event_ts_ms") is not None
                else None
            ),
            snapshot_id=str(payload.get("snapshot_id") or ""),
            raw_event_count=int(payload.get("raw_event_count") or 0),
            materialization_status=str(row.get("materialization_status") or "failed"),
            rebuild_reason=str(payload["rebuild_reason"]) if payload.get("rebuild_reason") else None,
            processed_event_ids=tuple(
                str(item) for item in list(payload.get("processed_event_ids") or []) if str(item)
            ),
        )

    def _projection_state_from_row(self, row: Mapping[str, Any]) -> ProjectionLoadResult[_UsageProjectionState]:
        payload = _decode_projection(row)
        checkpoint = self._projection_checkpoint_from_row(row)
        aggregates = {
            str(dimension): {
                str(key): dict(value)
                for key, value in dict(rows).items()
                if isinstance(value, dict)
            }
            for dimension, rows in dict(payload.get("aggregates") or {}).items()
            if isinstance(rows, dict)
        }
        return ProjectionLoadResult(
            state=_UsageProjectionState(aggregates=aggregates),
            checkpoint=checkpoint,
            payload=dict(payload),
        )

    def _create_state(self) -> _UsageProjectionState:
        return _UsageProjectionState(aggregates={})

    def _decode_event(self, payload_json: str) -> BudgetEvent:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("usage event payload must be an object")
        return budget_event_from_dict(payload)

    def _apply_event(self, state: _UsageProjectionState, event: BudgetEvent, _seq: int) -> None:
        for dimension, keys in _event_groups(event).items():
            dimension_rows = state.aggregates.setdefault(dimension, {})
            for key in keys:
                aggregate = dimension_rows.setdefault(key, _empty_aggregate())
                _merge_event(aggregate, event)

    def _build_payload(
        self,
        state: _UsageProjectionState,
        checkpoint: ProjectionCheckpoint,
        processed_event_ids: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "projection_id": checkpoint.projection_id,
            "workspace_id": checkpoint.workspace_id,
            "projection_schema_version": checkpoint.projection_schema_version,
            "source_namespace": checkpoint.source_namespace,
            "source_from_seq": checkpoint.source_from_seq,
            "source_to_seq": checkpoint.source_to_seq,
            "projected_at_ms": checkpoint.projected_at_ms,
            "last_source_event_ts_ms": checkpoint.last_source_event_ts_ms,
            "snapshot_id": checkpoint.snapshot_id,
            "raw_event_count": checkpoint.raw_event_count,
            "rebuild_reason": checkpoint.rebuild_reason,
            "processed_event_ids": list(processed_event_ids),
            "aggregates": state.aggregates,
        }

    def _build_snapshot(self, row: Mapping[str, Any]) -> UsageProjectionSnapshot:
        return UsageProjectionSnapshot(
            projection_id=str(row["payload"].get("projection_id") or self.projection_id),
            workspace_id=str(row["payload"].get("workspace_id") or self.workspace_id),
            projection_schema_version=int(
                row["payload"].get("projection_schema_version") or USAGE_PROJECTION_SCHEMA_VERSION
            ),
            source_namespace=str(row["payload"].get("source_namespace") or self.source_namespace),
            source_from_seq=int(row["payload"].get("source_from_seq") or 0),
            source_to_seq=int(row["payload"].get("source_to_seq") or 0),
            last_authoritative_seq=int(row.get("last_authoritative_seq") or 0),
            last_materialized_seq=int(row.get("last_materialized_seq") or 0),
            projected_at_ms=(
                int(row["payload"]["projected_at_ms"])
                if row["payload"].get("projected_at_ms") is not None
                else None
            ),
            last_source_event_ts_ms=(
                int(row["payload"]["last_source_event_ts_ms"])
                if row["payload"].get("last_source_event_ts_ms") is not None
                else None
            ),
            snapshot_id=str(row["payload"].get("snapshot_id") or ""),
            raw_event_count=int(row["payload"].get("raw_event_count") or 0),
            materialization_status=str(row.get("materialization_status") or "failed"),
            rebuild_reason=str(row["payload"]["rebuild_reason"]) if row["payload"].get("rebuild_reason") else None,
            aggregates={
                str(dimension): {
                    str(key): dict(value)
                    for key, value in dict(rows).items()
                    if isinstance(value, dict)
                }
                for dimension, rows in dict(row["payload"].get("aggregates") or {}).items()
                if isinstance(rows, dict)
            },
        )

    def snapshot(self) -> UsageProjectionSnapshot | None:
        row = self.meta.get_named_projection(self.projection_namespace, self.workspace_id)
        if not row:
            return None
        self._projection_checkpoint_from_row(row)
        return self._build_snapshot(row)

    def refresh(self, *, rebuild_from_scratch: bool = False) -> UsageProjectionSnapshot:
        return refresh_checkpointed_named_projection(
            self.meta,
            namespace=self.projection_namespace,
            key=self.workspace_id,
            projection_id=self.projection_id,
            workspace_id=self.workspace_id,
            source_namespace=self.source_namespace,
            projection_schema_version=USAGE_PROJECTION_SCHEMA_VERSION,
            decode_current=self._projection_state_from_row,
            create_state=self._create_state,
            decode_event=self._decode_event,
            event_key=_event_id,
            apply_event=self._apply_event,
            build_payload=self._build_payload,
            build_snapshot=self._build_snapshot,
            include_event=lambda entity_kind, _entity_id, _op, _payload_json: entity_kind == USAGE_EVENT_KIND,
            rebuild_from_scratch=rebuild_from_scratch,
        )
