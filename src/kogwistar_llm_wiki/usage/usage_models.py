"""Storage contracts and public value objects for usage projections."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol


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
