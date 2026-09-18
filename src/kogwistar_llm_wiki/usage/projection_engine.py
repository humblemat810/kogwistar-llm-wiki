"""Incremental, checkpointed usage projection engine."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kogwistar.runtime import (
    BudgetEvent,
    ProjectionCheckpoint,
    ProjectionLoadResult,
    budget_event_from_dict,
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
