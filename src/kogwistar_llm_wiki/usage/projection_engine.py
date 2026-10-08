"""Incremental, checkpointed usage projection engine."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from kogwistar.json_types import JsonObject, JsonValue
from kogwistar.runtime import (
    BudgetEvent,
    ProjectionCheckpoint,
    ProjectionLoadResult,
)
from kogwistar.runtime.budget import budget_event_from_dict
from kogwistar.runtime.checkpointed_projection import (
    refresh_checkpointed_named_projection,
)

from .aggregation import USAGE_PROJECTION_SCHEMA_VERSION
from .aggregation import decode_projection as _decode_projection
from .aggregation import empty_aggregate as _empty_aggregate
from .aggregation import event_groups as _event_groups
from .aggregation import merge_event as _merge_event
from .events import (
    USAGE_EVENT_KIND,
    _event_id,
    append_usage_event,  # noqa: F401 - compatibility export
    persist_usage_events,  # noqa: F401 - compatibility export
)
from .usage_models import UsageMetaStore, UsageProjectionSnapshot


@dataclass(frozen=True, slots=True)
class _UsageProjectionState:
    aggregates: dict[str, dict[str, dict[str, object]]]


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _as_aggregate_map(value: object) -> dict[str, dict[str, dict[str, object]]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, dict[str, object]]] = {}
    for dimension, rows in value.items():
        if not isinstance(rows, dict):
            continue
        result[str(dimension)] = {
            str(key): dict(aggregate)
            for key, aggregate in rows.items()
            if isinstance(aggregate, dict)
        }
    return result


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise TypeError(f"usage projection contains non-JSON value: {type(value).__name__}")


def _json_object(values: Mapping[str, object]) -> JsonObject:
    return cast(JsonObject, {str(key): _json_value(value) for key, value in values.items()})


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

    def _projection_checkpoint_from_row(self, row: Mapping[str, JsonValue]) -> ProjectionCheckpoint:
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
            projection_schema_version=_as_int(
                payload.get("projection_schema_version"), USAGE_PROJECTION_SCHEMA_VERSION
            ),
            source_namespace=str(payload.get("source_namespace") or self.source_namespace),
            source_from_seq=_as_int(payload.get("source_from_seq")),
            source_to_seq=_as_int(payload.get("source_to_seq")),
            last_authoritative_seq=_as_int(row.get("last_authoritative_seq")),
            last_materialized_seq=_as_int(row.get("last_materialized_seq")),
            projected_at_ms=(
                _as_int(payload["projected_at_ms"])
                if payload.get("projected_at_ms") is not None
                else None
            ),
            last_source_event_ts_ms=(
                _as_int(payload["last_source_event_ts_ms"])
                if payload.get("last_source_event_ts_ms") is not None
                else None
            ),
            snapshot_id=str(payload.get("snapshot_id") or ""),
            raw_event_count=_as_int(payload.get("raw_event_count")),
            materialization_status=str(row.get("materialization_status") or "failed"),
            rebuild_reason=str(payload["rebuild_reason"]) if payload.get("rebuild_reason") else None,
            processed_event_ids=tuple(
                _as_string_list(payload.get("processed_event_ids")),
            ),
        )

    def _projection_state_from_row(
        self, row: Mapping[str, JsonValue]
    ) -> ProjectionLoadResult[_UsageProjectionState]:
        payload = _decode_projection(row)
        checkpoint = self._projection_checkpoint_from_row(row)
        return ProjectionLoadResult(
            state=_UsageProjectionState(aggregates=_as_aggregate_map(payload.get("aggregates"))),
            checkpoint=checkpoint,
            payload=payload,
        )

    def _create_state(self) -> _UsageProjectionState:
        return _UsageProjectionState(aggregates={})

    def _decode_event(self, payload_json: str) -> BudgetEvent:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("usage event payload must be an object")  # noqa: TRY004
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
    ) -> JsonObject:
        return _json_object({
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
        })

    def _build_snapshot(self, row: Mapping[str, JsonValue]) -> UsageProjectionSnapshot:
        payload = _decode_projection(row)
        return UsageProjectionSnapshot(
            projection_id=str(payload.get("projection_id") or self.projection_id),
            workspace_id=str(payload.get("workspace_id") or self.workspace_id),
            projection_schema_version=_as_int(
                payload.get("projection_schema_version"), USAGE_PROJECTION_SCHEMA_VERSION
            ),
            source_namespace=str(payload.get("source_namespace") or self.source_namespace),
            source_from_seq=_as_int(payload.get("source_from_seq")),
            source_to_seq=_as_int(payload.get("source_to_seq")),
            last_authoritative_seq=_as_int(row.get("last_authoritative_seq")),
            last_materialized_seq=_as_int(row.get("last_materialized_seq")),
            projected_at_ms=(
                _as_int(payload["projected_at_ms"])
                if payload.get("projected_at_ms") is not None
                else None
            ),
            last_source_event_ts_ms=(
                _as_int(payload["last_source_event_ts_ms"])
                if payload.get("last_source_event_ts_ms") is not None
                else None
            ),
            snapshot_id=str(payload.get("snapshot_id") or ""),
            raw_event_count=_as_int(payload.get("raw_event_count")),
            materialization_status=str(row.get("materialization_status") or "failed"),
            rebuild_reason=str(payload["rebuild_reason"]) if payload.get("rebuild_reason") else None,
            aggregates=_as_aggregate_map(payload.get("aggregates")),
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
