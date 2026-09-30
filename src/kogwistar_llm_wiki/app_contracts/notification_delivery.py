"""Durable, ACL-rechecked delivery over Kogwistar's shared jobs queue."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from kogwistar.engine_core.jobs import JobQueueItem
from kogwistar.id_provider import stable_id

from ..configuration.workspace import WorkspaceNamespaces
from ..models import NamespaceEngines
from .notification_digest import NotificationDigest, NotificationDigestItem


class NotificationDeliveryAdapter(Protocol):
    """External channel sender.

    Use the stable key for destination-supported deduplication. Destinations
    without idempotency support remain at-least-once and may receive retries.
    """

    def deliver(
        self,
        *,
        recipient_id: str,
        digest: NotificationDigest,
        idempotency_key: str,
    ) -> None: ...


AuthorizeRecipient = Callable[[str, str], bool]
# Source access is principal-scoped; workspace membership is not authorization.
AuthorizeSource = Callable[[str, str, str], bool]


@dataclass(frozen=True, slots=True)
class NotificationDeliveryOutcome:
    job_id: str
    status: str
    error_code: str | None = None


class NotificationDeliveryScheduler:
    """Queue bounded digests and deliver with the existing durable job queue.

    Routine items travel as one closed-window digest. Urgent items each get a
    separate job, so they are neither grouped nor held behind routine content.
    Delivery is at-least-once; adapters should use the stable job ID as their
    idempotency key where the destination supports it.
    """

    _JOB_KIND = "notification_delivery"
    _MAX_PAYLOAD_BYTES = 256 * 1024
    _ROUTINE_DETAIL_LIMIT = 5

    def __init__(self, *, engines: NamespaceEngines) -> None:
        self.engines = engines

    def enqueue_digest(
        self,
        *,
        recipient_id: str,
        digest: NotificationDigest,
        authorize_recipient: AuthorizeRecipient,
        authorize_source: AuthorizeSource,
        max_retries: int = 5,
    ) -> tuple[str, ...]:
        recipient = _required(recipient_id, "recipient_id")
        if not isinstance(digest, NotificationDigest):
            raise TypeError("digest must be NotificationDigest")
        if type(max_retries) is not int or max_retries < 1:
            raise ValueError("max_retries must be positive")
        self._authorize(recipient, digest, authorize_recipient, authorize_source)

        deliveries: list[tuple[str, dict[str, object]]] = []
        # Queue urgent events individually and first; routine items remain one
        # concise notification with full drill-down references.
        for item in digest.urgent:
            urgent_digest = NotificationDigest(
                workspace_id=digest.workspace_id,
                window_start=digest.window_start,
                window_end=digest.window_end,
                routine=(),
                urgent=(item,),
            )
            deliveries.append(("urgent", _serialize_digest(urgent_digest)))
        if digest.routine:
            routine_items = _compact_routine_items(
                digest.routine,
                workspace_id=digest.workspace_id,
                window_start=digest.window_start,
                window_end=digest.window_end,
            )
            routine_digest = NotificationDigest(
                workspace_id=digest.workspace_id,
                window_start=digest.window_start,
                window_end=digest.window_end,
                routine=routine_items,
                urgent=(),
            )
            deliveries.append(("routine", _serialize_digest(routine_digest)))

        queue = self.engines.conversation.jobs
        queue.require_available(enqueue=True)
        prepared: list[tuple[str, str, dict[str, object]]] = []
        for delivery_class, serialized in deliveries:
            payload: dict[str, object] = {
                "job_type": self._JOB_KIND,
                "workspace_id": digest.workspace_id,
                "recipient_id": recipient,
                "delivery_class": delivery_class,
                "digest": serialized,
            }
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > self._MAX_PAYLOAD_BYTES:
                raise ValueError("notification delivery payload exceeds configured safety bound")
            job_id = str(
                stable_id(
                    "kogwistar_llm_wiki.notification_delivery",
                    digest.workspace_id,
                    recipient,
                    delivery_class,
                    encoded,
                )
            )
            prepared.append((delivery_class, job_id, payload))

        namespaces = WorkspaceNamespaces(digest.workspace_id)
        job_ids: list[str] = []
        uow = getattr(self.engines.conversation, "uow", None)
        if not callable(uow):
            raise TypeError("conversation engine must expose uow() for atomic notification enqueue")
        with uow():
            for delivery_class, job_id, payload in prepared:
                job_ids.append(
                    queue.enqueue(
                        job_id=job_id,
                        namespace=(
                            namespaces.urgent_notification_jobs
                            if delivery_class == "urgent"
                            else namespaces.notification_jobs
                        ),
                        # index_jobs coalesces pending work by namespace plus
                        # entity_kind/entity_id/job_kind. Give each immutable
                        # delivery its own identity; recipient-wide identity would
                        # overwrite sibling urgent jobs and the routine digest.
                        entity_kind="notification_delivery",
                        entity_id=job_id,
                        job_kind=self._JOB_KIND,
                        op="DELIVER",
                        payload=payload,
                        max_retries=max_retries,
                    )
                )
        return tuple(job_ids)

    def process_pending_jobs(
        self,
        *,
        workspace_id: str,
        adapter: NotificationDeliveryAdapter,
        authorize_recipient: AuthorizeRecipient,
        authorize_source: AuthorizeSource,
        limit: int = 20,
        queue_lease_seconds: int = 60,
    ) -> tuple[NotificationDeliveryOutcome, ...]:
        workspace = _required(workspace_id, "workspace_id")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        if type(queue_lease_seconds) is not int or queue_lease_seconds < 1:
            raise ValueError("queue_lease_seconds must be positive")
        if not callable(getattr(adapter, "deliver", None)):
            raise TypeError("adapter must implement deliver()")

        queue = self.engines.conversation.jobs
        queue.require_available(claim=True)
        namespaces = WorkspaceNamespaces(workspace)
        urgent_jobs = queue.claim(
            namespace=namespaces.urgent_notification_jobs,
            limit=limit,
            lease_seconds=queue_lease_seconds,
        )
        jobs = urgent_jobs
        if len(jobs) < limit:
            jobs = [
                *jobs,
                *queue.claim(
                    namespace=namespaces.notification_jobs,
                    limit=limit - len(jobs),
                    lease_seconds=queue_lease_seconds,
                ),
            ]
        outcomes: list[NotificationDeliveryOutcome] = []
        for job in jobs:
            try:
                recipient, digest = _parse_job(job, workspace_id=workspace)
                self._authorize(recipient, digest, authorize_recipient, authorize_source)
            except PermissionError:
                queue.mark_failed(
                    job.job_id,
                    "notification authorization revoked or denied",
                    final=True,
                    claim_token=job.claim_token,
                )
                outcomes.append(
                    NotificationDeliveryOutcome(job_id=job.job_id, status="denied", error_code="acl_denied")
                )
                continue
            except (KeyError, TypeError, ValueError):
                queue.mark_failed(
                    job.job_id,
                    "invalid notification delivery job",
                    final=True,
                    claim_token=job.claim_token,
                )
                outcomes.append(
                    NotificationDeliveryOutcome(job_id=job.job_id, status="failed", error_code="invalid_job")
                )
                continue
            except Exception as exc:  # noqa: BLE001 - ACL adapters may be temporarily unavailable
                queue.retry_or_fail(job, exc)
                outcomes.append(
                    NotificationDeliveryOutcome(
                        job_id=job.job_id,
                        status="retrying",
                        error_code="authorization_unavailable",
                    )
                )
                continue

            try:
                adapter.deliver(
                    recipient_id=recipient,
                    digest=digest,
                    idempotency_key=job.job_id,
                )
            except Exception as exc:  # noqa: BLE001 - shared queue owns retry/DLQ policy
                queue.retry_or_fail(job, exc)
                outcomes.append(
                    NotificationDeliveryOutcome(
                        job_id=job.job_id,
                        status="retrying",
                        error_code=type(exc).__name__,
                    )
                )
                continue
            acknowledged = queue.mark_done(job.job_id, claim_token=job.claim_token)
            outcomes.append(
                NotificationDeliveryOutcome(
                    job_id=job.job_id,
                    status="delivered" if acknowledged else "stale_claim",
                )
            )
        return tuple(outcomes)

    @staticmethod
    def _authorize(
        recipient_id: str,
        digest: NotificationDigest,
        authorize_recipient: AuthorizeRecipient,
        authorize_source: AuthorizeSource,
    ) -> None:
        if not callable(authorize_recipient) or not callable(authorize_source):
            raise TypeError("recipient and source ACL callbacks are required")
        if not authorize_recipient(digest.workspace_id, recipient_id):
            raise PermissionError("notification recipient is not authorized")
        source_ids = {
            source_id
            for item in (*digest.routine, *digest.urgent)
            for source_id, _revision_id, _event_id in item.evidence_refs
        }
        if not source_ids:
            raise ValueError("notification digest must reference at least one source")
        for source_id in sorted(source_ids):
            if not authorize_source(digest.workspace_id, recipient_id, source_id):
                raise PermissionError("notification source is not authorized")


def _serialize_digest(digest: NotificationDigest) -> dict[str, object]:
    def item_payload(item: NotificationDigestItem) -> dict[str, object]:
        return {
            "item_id": item.item_id,
            "workspace_id": item.workspace_id,
            "summary": item.summary,
            "severity": item.severity,
            "action_required": item.action_required,
            "source_event_ids": list(item.source_event_ids),
            "evidence_refs": [list(reference) for reference in item.evidence_refs],
            "urgent": item.urgent,
            "logical_event_count": item.logical_event_count,
            "duplicate_copy_count": item.duplicate_copy_count,
        }

    return {
        "workspace_id": digest.workspace_id,
        "window_start": digest.window_start.isoformat(),
        "window_end": digest.window_end.isoformat(),
        "routine": [item_payload(item) for item in digest.routine],
        "urgent": [item_payload(item) for item in digest.urgent],
    }


def _compact_routine_items(
    items: tuple[NotificationDigestItem, ...],
    *,
    workspace_id: str,
    window_start: datetime,
    window_end: datetime,
) -> tuple[NotificationDigestItem, ...]:
    """Collapse routine display rows while retaining complete drill-down refs."""

    if len(items) <= NotificationDeliveryScheduler._ROUTINE_DETAIL_LIMIT:
        return items
    source_event_ids = tuple(sorted({event_id for item in items for event_id in item.source_event_ids}))
    evidence_refs = tuple(
        sorted({reference for item in items for reference in item.evidence_refs})
    )
    logical_event_count = sum(item.logical_event_count for item in items)
    duplicate_copy_count = sum(item.duplicate_copy_count for item in items)
    severity_rank = {"info": 0, "normal": 1, "high": 2, "critical": 3}
    severity = max((item.severity for item in items), key=severity_rank.__getitem__)
    action_required = any(item.action_required for item in items)
    summary = f"{logical_event_count} routine updates across {len(items)} groups"
    if duplicate_copy_count:
        copy_label = "copy" if duplicate_copy_count == 1 else "copies"
        summary += f"; {duplicate_copy_count} duplicate {copy_label} suppressed"
    if action_required:
        summary += "; some require action"
    identity = json.dumps(
        [
            workspace_id,
            window_start.isoformat(),
            window_end.isoformat(),
            sorted(item.item_id for item in items),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    item_id = "notification-rollup:" + str(
        stable_id("kogwistar_llm_wiki.notification_rollup", identity)
    )
    return (
        NotificationDigestItem(
            item_id=item_id,
            workspace_id=workspace_id,
            summary=summary,
            severity=severity,  # type: ignore[arg-type]
            action_required=action_required,
            source_event_ids=source_event_ids,
            evidence_refs=evidence_refs,
            urgent=False,
            logical_event_count=logical_event_count,
            duplicate_copy_count=duplicate_copy_count,
        ),
    )


def _parse_job(job: JobQueueItem, *, workspace_id: str) -> tuple[str, NotificationDigest]:
    payload = job.payload
    if payload.get("job_type") != NotificationDeliveryScheduler._JOB_KIND:
        raise ValueError("wrong job type")
    namespaces = WorkspaceNamespaces(workspace_id)
    delivery_class = payload.get("delivery_class")
    allowed_namespaces = (
        {namespaces.notification_jobs, namespaces.urgent_notification_jobs}
        if delivery_class == "urgent"
        else {namespaces.notification_jobs}
    )
    if str(payload.get("workspace_id") or "") != workspace_id or job.namespace not in (
        allowed_namespaces
    ):
        raise ValueError("job workspace mismatch")
    recipient = _required(str(payload.get("recipient_id") or ""), "recipient_id")
    valid_delivery_identity = (
        job.entity_kind == "notification_delivery" and job.entity_id == job.job_id
    )
    valid_legacy_identity = (
        job.entity_kind == "notification_recipient" and job.entity_id == recipient
    )
    if not (valid_delivery_identity or valid_legacy_identity) or job.job_kind != (
        NotificationDeliveryScheduler._JOB_KIND
    ):
        raise ValueError("job delivery identity/type mismatch")
    digest = _deserialize_digest(payload.get("digest"), workspace_id=workspace_id)
    if delivery_class == "urgent":
        if len(digest.urgent) != 1 or digest.routine:
            raise ValueError("urgent job must contain exactly one urgent item")
    elif delivery_class == "routine":
        if digest.urgent or not digest.routine:
            raise ValueError("routine job must contain routine items only")
    else:
        raise ValueError("delivery class is invalid")
    return recipient, digest


def parse_notification_delivery_job(
    job: JobQueueItem,
    *,
    workspace_id: str,
) -> tuple[str, NotificationDigest]:
    """Validate a durable delivery job for both workers and outbox readers."""
    return _parse_job(job, workspace_id=workspace_id)


def _deserialize_digest(raw: object, *, workspace_id: str) -> NotificationDigest:
    if not isinstance(raw, Mapping) or str(raw.get("workspace_id") or "") != workspace_id:
        raise ValueError("digest workspace mismatch")

    def parse_items(name: str, *, urgent: bool) -> tuple[NotificationDigestItem, ...]:
        values = raw.get(name)
        if not isinstance(values, list):
            raise TypeError("digest item list is invalid")
        parsed: list[NotificationDigestItem] = []
        for value in values:
            if not isinstance(value, Mapping):
                raise TypeError("digest item is invalid")
            refs = value.get("evidence_refs")
            event_ids = value.get("source_event_ids")
            if not isinstance(refs, list) or not isinstance(event_ids, list):
                raise TypeError("digest evidence references are invalid")
            if any(not isinstance(event_id, str) for event_id in event_ids):
                raise TypeError("digest event ID is invalid")
            evidence_refs = tuple(
                tuple(_required(str(part), "evidence reference") for part in ref)
                for ref in refs
                if isinstance(ref, list) and len(ref) == 3
            )
            if len(evidence_refs) != len(refs):
                raise ValueError("digest evidence reference shape is invalid")
            if tuple(sorted(event_ids)) != tuple(sorted(ref[2] for ref in evidence_refs)):
                raise ValueError("digest event/evidence references disagree")
            if type(value.get("action_required")) is not bool or value.get("urgent") is not urgent:
                raise ValueError("digest flags are invalid")
            logical_count = value.get("logical_event_count", len(event_ids))
            duplicate_count = value.get("duplicate_copy_count", 0)
            if type(logical_count) is not int or type(duplicate_count) is not int:
                raise ValueError("digest event counts are invalid")
            severity = value.get("severity")
            if severity not in ("info", "normal", "high", "critical"):
                raise ValueError("digest severity is invalid")
            parsed.append(
                NotificationDigestItem(
                    item_id=_required(str(value.get("item_id") or ""), "item_id"),
                    workspace_id=_required(str(value.get("workspace_id") or ""), "workspace_id"),
                    summary=_required(str(value.get("summary") or ""), "summary"),
                    severity=severity,  # type: ignore[arg-type]
                    action_required=value["action_required"],
                    source_event_ids=tuple(_required(str(item), "event_id") for item in event_ids),
                    evidence_refs=evidence_refs,
                    urgent=urgent,
                    logical_event_count=logical_count,
                    duplicate_copy_count=duplicate_count,
                )
            )
            if parsed[-1].workspace_id != workspace_id:
                raise ValueError("digest item workspace mismatch")
        return tuple(parsed)

    start = _parse_datetime(raw.get("window_start"))
    end = _parse_datetime(raw.get("window_end"))
    if start >= end:
        raise ValueError("digest window is invalid")
    routine = parse_items("routine", urgent=False)
    urgent_items = parse_items("urgent", urgent=True)
    if any(item.severity in ("high", "critical") for item in routine) or any(
        item.severity not in ("high", "critical") for item in urgent_items
    ):
        raise ValueError("digest urgency classification is invalid")
    return NotificationDigest(
        workspace_id=workspace_id,
        window_start=start,
        window_end=end,
        routine=routine,
        urgent=urgent_items,
    )


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("digest timestamp is invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("digest timestamp must be timezone-aware")
    return parsed


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{name} must not contain control characters")
    return value.strip()


__all__ = [
    "NotificationDeliveryAdapter",
    "NotificationDeliveryOutcome",
    "NotificationDeliveryScheduler",
    "parse_notification_delivery_job",
]
