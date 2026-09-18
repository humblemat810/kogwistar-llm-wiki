"""Idempotent raw usage-event persistence."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace

from kogwistar.id_provider import stable_id
from kogwistar.runtime import BudgetAttribution, BudgetEvent, budget_event_to_dict

from .usage_models import UsageMetaStore

USAGE_EVENT_KIND = "usage_event"


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
            source_document_id=source_document_id
            if source_document_id is not None
            else existing.source_document_id,
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
