"""Incremental, checkpointed usage projections for llm-wiki."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from kogwistar.id_provider import stable_id
from kogwistar.runtime.budget import (
    BudgetEvent,
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


def _now_ms() -> int:
    return int(time.time() * 1000)


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


def _empty_aggregate() -> dict[str, object]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
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
    aggregate["total_cost"] = round(
        float(aggregate.get("total_cost", 0.0) or 0.0)
        + float(summary.get("total_cost", 0.0) or 0.0),
        6,
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

    def snapshot(self) -> UsageProjectionSnapshot | None:
        row = self.meta.get_named_projection(self.projection_namespace, self.workspace_id)
        if not row:
            return None
        payload = _decode_projection(row)
        return UsageProjectionSnapshot(
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
            aggregates={
                str(dimension): {
                    str(key): dict(value)
                    for key, value in dict(rows).items()
                    if isinstance(value, dict)
                }
                for dimension, rows in dict(payload.get("aggregates") or {}).items()
                if isinstance(rows, dict)
            },
        )

    def refresh(self, *, rebuild_from_scratch: bool = False) -> UsageProjectionSnapshot:
        rebuild_reason: str | None = None
        try:
            current = self.snapshot()
        except (TypeError, ValueError):
            current = None
            rebuild_reason = "incompatible_projection"
        if rebuild_from_scratch:
            current = None
            rebuild_reason = "explicit_rebuild"
        elif current is None and rebuild_reason is None:
            rebuild_reason = "missing_projection"

        latest = self.meta.get_latest_entity_event_seq(namespace=self.source_namespace)
        if current is not None and current.last_authoritative_seq > latest:
            current = None
            rebuild_reason = "source_sequence_regressed"

        from_seq = 1 if current is None else current.last_materialized_seq + 1
        to_seq = int(latest)
        aggregates = (
            {dimension: {key: dict(value) for key, value in rows.items()} for dimension, rows in current.aggregates.items()}
            if current is not None
            else {}
        )
        processed_ids: set[str] = set()
        if current is not None:
            previous_payload = self.meta.get_named_projection(self.projection_namespace, self.workspace_id) or {}
            previous_body = dict(previous_payload.get("payload") or {})
            processed_ids = {str(item) for item in previous_body.get("processed_event_ids", []) if str(item)}
        committed_processed_ids = set(processed_ids)

        raw_event_count = current.raw_event_count if current is not None else 0
        last_source_event_ts_ms = current.last_source_event_ts_ms if current is not None else None
        try:
            for seq, entity_kind, _entity_id, _op, payload_json in self.meta.iter_entity_events(
                namespace=self.source_namespace,
                from_seq=from_seq,
                to_seq=to_seq,
            ):
                if entity_kind != USAGE_EVENT_KIND:
                    continue
                payload = json.loads(payload_json)
                if not isinstance(payload, dict):
                    continue
                event = budget_event_from_dict(payload)
                event_id = _event_id(event)
                if event_id in processed_ids:
                    continue
                processed_ids.add(event_id)
                raw_event_count += 1
                if event.ts_ms is not None:
                    last_source_event_ts_ms = max(last_source_event_ts_ms or event.ts_ms, event.ts_ms)
                for dimension, keys in _event_groups(event).items():
                    dimension_rows = aggregates.setdefault(dimension, {})
                    for key in keys:
                        aggregate = dimension_rows.setdefault(key, _empty_aggregate())
                        _merge_event(aggregate, event)

            projected_at_ms = _now_ms()
            authoritative_after = self.meta.get_latest_entity_event_seq(namespace=self.source_namespace)
            status = "materialized" if authoritative_after <= to_seq else "catching_up"
            body = {
                "projection_id": self.projection_id,
                "workspace_id": self.workspace_id,
                "projection_schema_version": USAGE_PROJECTION_SCHEMA_VERSION,
                "source_namespace": self.source_namespace,
                "source_from_seq": from_seq,
                "source_to_seq": to_seq,
                "projected_at_ms": projected_at_ms,
                "last_source_event_ts_ms": last_source_event_ts_ms,
                "snapshot_id": str(stable_id("usage_snapshot", self.workspace_id, self.projection_id, to_seq)),
                "raw_event_count": raw_event_count,
                "rebuild_reason": rebuild_reason,
                "processed_event_ids": sorted(processed_ids),
                "aggregates": aggregates,
            }
            self.meta.replace_named_projection(
                self.projection_namespace,
                self.workspace_id,
                body,
                last_authoritative_seq=authoritative_after,
                last_materialized_seq=to_seq,
                projection_schema_version=USAGE_PROJECTION_SCHEMA_VERSION,
                materialization_status=status,
            )
            result = self.snapshot()
            if result is None:
                raise RuntimeError("usage projection disappeared after materialization")
            return result
        except Exception as exc:
            if current is None:
                raise
            failed_body = {
                "projection_id": current.projection_id,
                "workspace_id": current.workspace_id,
                "projection_schema_version": current.projection_schema_version,
                "source_namespace": current.source_namespace,
                "source_from_seq": current.source_from_seq,
                "source_to_seq": current.source_to_seq,
                "projected_at_ms": current.projected_at_ms,
                "last_source_event_ts_ms": current.last_source_event_ts_ms,
                "snapshot_id": current.snapshot_id,
                "raw_event_count": current.raw_event_count,
                "rebuild_reason": f"projection_failed:{type(exc).__name__}",
                "processed_event_ids": sorted(committed_processed_ids),
                "aggregates": current.aggregates,
            }
            self.meta.replace_named_projection(
                self.projection_namespace,
                self.workspace_id,
                failed_body,
                last_authoritative_seq=current.last_authoritative_seq,
                last_materialized_seq=current.last_materialized_seq,
                projection_schema_version=current.projection_schema_version,
                materialization_status="failed",
            )
            raise
