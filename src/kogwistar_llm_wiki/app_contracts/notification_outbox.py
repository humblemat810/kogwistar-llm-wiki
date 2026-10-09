"""ACL-filtered, bounded history for notification delivery jobs."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from kogwistar.engine_core.jobs import JobQueueItem

from ..configuration.workspace import WorkspaceNamespaces
from ..models import NamespaceEngines
from .notification_delivery import parse_notification_delivery_job

DeliveryClass = Literal["routine", "urgent"]

class AuthorizeOutboxRecipient(Protocol):
    """Authorize delivery-history access for one workspace recipient."""

    def __call__(self, workspace_id: str, principal_id: str, /) -> bool: ...


class AuthorizeOutboxSource(Protocol):
    """Authorize access to one source referenced by a delivery."""

    def __call__(self, workspace_id: str, principal_id: str, source_id: str, /) -> bool: ...


class NotificationOutboxUnavailableError(RuntimeError):
    """Raised when the selected Kogwistar backend lacks ordered history."""


class _JobPageLike(Protocol):
    items: tuple[JobQueueItem, ...] | list[JobQueueItem]
    next_cursor: object | None


class _JobPageReader(Protocol):
    def __call__(
        self,
        *,
        namespace: str,
        status: str | None,
        entity_kind: str,
        job_kind: str,
        limit: int,
        cursor: object | None,
    ) -> _JobPageLike: ...


class _JobQueueCursorFactory(Protocol):
    def __call__(
        self,
        *,
        created_at_us: int,
        job_id: str,
        filter_fingerprint: str,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class NotificationOutboxDigestItem:
    summary: str
    severity: str
    action_required: bool
    urgent: bool
    logical_event_count: int
    duplicate_copy_count: int
    evidence_ref_count: int


@dataclass(frozen=True, slots=True)
class NotificationOutboxEntry:
    delivery_id: str
    delivery_class: DeliveryClass
    status: str
    created_at_ms: int | None
    updated_at_ms: int | None
    retry_count: int
    max_retries: int
    claim_attempts: int
    failure_reported: bool
    summaries: tuple[str, ...]
    digest_items: tuple[NotificationOutboxDigestItem, ...]
    severity: str
    action_required: bool
    logical_event_count: int
    duplicate_copy_count: int
    evidence_ref_count: int


@dataclass(frozen=True, slots=True)
class NotificationOutboxPage:
    items: tuple[NotificationOutboxEntry, ...]
    next_cursor: str | None


class NotificationOutboxReader:
    """Read own delivery history; queue remains authoritative for job state."""

    _MAX_LIMIT = 100
    _MAX_CURSOR_CHARS = 4096
    _MAX_SUMMARY_CHARS = 360

    def __init__(
        self,
        *,
        engines: NamespaceEngines,
        authorize_recipient: AuthorizeOutboxRecipient,
        authorize_source: AuthorizeOutboxSource,
    ) -> None:
        if not callable(authorize_recipient) or not callable(authorize_source):
            raise TypeError("notification outbox ACL callbacks are required")
        self.engines = engines
        self.authorize_recipient = authorize_recipient
        self.authorize_source = authorize_source

    def list_page(
        self,
        *,
        workspace_id: str,
        principal_id: str,
        delivery_class: DeliveryClass,
        status: str | None = None,
        limit: int = 30,
        cursor: str | None = None,
    ) -> NotificationOutboxPage:
        workspace = _required(workspace_id, "workspace_id")
        principal = _required(principal_id, "principal_id")
        if delivery_class not in ("routine", "urgent"):
            raise ValueError("delivery_class must be routine or urgent")
        if status not in (None, "PENDING", "DOING", "DONE", "FAILED"):
            raise ValueError("unsupported notification status")
        if type(limit) is not int or not 1 <= limit <= self._MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {self._MAX_LIMIT}")

        # Deny before touching the queue; every returned row is also source-checked.
        if self.authorize_recipient(workspace, principal) is not True:
            raise PermissionError("notification recipient is not authorized")

        namespaces = WorkspaceNamespaces(workspace)
        namespace = (
            namespaces.urgent_notification_jobs
            if delivery_class == "urgent"
            else namespaces.notification_jobs
        )
        queue = self.engines.conversation.jobs
        page_reader = getattr(queue, "list_page", None)
        if not callable(page_reader):
            raise NotificationOutboxUnavailableError(
                "notification outbox requires ordered job history support from Kogwistar core"
            )
        queue_cursor = self._decode_cursor(
            cursor,
            workspace_id=workspace,
            principal_id=principal,
            delivery_class=delivery_class,
            status=status,
        )
        read_page = cast(_JobPageReader, page_reader)
        page = read_page(
            namespace=namespace,
            status=status,
            entity_kind="notification_delivery",
            job_kind="notification_delivery",
            limit=limit,
            cursor=queue_cursor,
        )

        entries: list[NotificationOutboxEntry] = []
        for job in page.items:
            entry = self._entry(
                job,
                workspace_id=workspace,
                principal_id=principal,
                namespace=namespace,
                delivery_class=delivery_class,
            )
            if entry is not None:
                entries.append(entry)

        next_cursor = None
        if page.next_cursor is not None:
            next_cursor = self._encode_cursor(
                page.next_cursor,
                workspace_id=workspace,
                principal_id=principal,
                delivery_class=delivery_class,
                status=status,
            )
        return NotificationOutboxPage(items=tuple(entries), next_cursor=next_cursor)

    def _entry(
        self,
        job: JobQueueItem,
        *,
        workspace_id: str,
        principal_id: str,
        namespace: str,
        delivery_class: DeliveryClass,
    ) -> NotificationOutboxEntry | None:
        if (
            job.namespace != namespace
            or job.entity_kind != "notification_delivery"
            or job.entity_id != job.job_id
            or job.job_kind != "notification_delivery"
        ):
            return None
        try:
            recipient, digest = parse_notification_delivery_job(
                job,
                workspace_id=workspace_id,
            )
        except (KeyError, TypeError, ValueError):
            return None
        if recipient != principal_id:
            return None

        source_ids = {
            source_id
            for item in (*digest.routine, *digest.urgent)
            for source_id, _revision_id, _event_id in item.evidence_refs
        }
        for source_id in sorted(source_ids):
            if self.authorize_source(workspace_id, principal_id, source_id) is not True:
                return None

        digest_items = (*digest.routine, *digest.urgent)
        severity = max(
            (item.severity for item in digest_items),
            key={"info": 0, "normal": 1, "high": 2, "critical": 3}.__getitem__,
        )
        raw_status = getattr(job, "status", None)
        status = (
            raw_status
            if isinstance(raw_status, str)
            and raw_status in {"PENDING", "DOING", "DONE", "FAILED"}
            else "UNKNOWN"
        )
        return NotificationOutboxEntry(
            delivery_id=job.job_id,
            delivery_class=delivery_class,
            status=status,
            created_at_ms=getattr(job, "created_at_ms", None),
            updated_at_ms=getattr(job, "updated_at_ms", None),
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            claim_attempts=getattr(job, "claim_attempts", 0),
            failure_reported=bool(job.last_error),
            summaries=tuple(
                _bounded_text(item.summary, self._MAX_SUMMARY_CHARS)
                for item in digest_items
            ),
            digest_items=tuple(
                NotificationOutboxDigestItem(
                    summary=_bounded_text(item.summary, self._MAX_SUMMARY_CHARS),
                    severity=item.severity,
                    action_required=item.action_required,
                    urgent=item.urgent,
                    logical_event_count=item.logical_event_count,
                    duplicate_copy_count=item.duplicate_copy_count,
                    evidence_ref_count=len(item.evidence_refs),
                )
                for item in digest_items
            ),
            severity=severity,
            action_required=any(item.action_required for item in digest_items),
            logical_event_count=sum(item.logical_event_count for item in digest_items),
            duplicate_copy_count=sum(item.duplicate_copy_count for item in digest_items),
            evidence_ref_count=sum(len(item.evidence_refs) for item in digest_items),
        )

    @staticmethod
    def _encode_cursor(
        cursor: object,
        *,
        workspace_id: str,
        principal_id: str,
        delivery_class: DeliveryClass,
        status: str | None,
    ) -> str:
        payload = {
            "v": 1,
            "workspace_id": workspace_id,
            "principal_id": principal_id,
            "delivery_class": delivery_class,
            "status": status,
            "created_at_us": getattr(cursor, "created_at_us", None),
            "job_id": getattr(cursor, "job_id", None),
            "filter_fingerprint": getattr(cursor, "filter_fingerprint", None),
        }
        if (
            type(payload["created_at_us"]) is not int
            or not isinstance(payload["job_id"], str)
            or not isinstance(payload["filter_fingerprint"], str)
        ):
            raise NotificationOutboxUnavailableError(
                "Kogwistar returned an invalid ordered-job cursor"
            )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")

    def _decode_cursor(
        self,
        token: str | None,
        *,
        workspace_id: str,
        principal_id: str,
        delivery_class: DeliveryClass,
        status: str | None,
    ) -> object | None:
        if token is None:
            return None
        if not isinstance(token, str) or not token or len(token) > self._MAX_CURSOR_CHARS:
            raise ValueError("notification outbox cursor is invalid")
        try:
            raw = base64.b64decode(
                token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
            )
            payload = json.loads(raw)
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("notification outbox cursor is invalid") from exc
        expected = {
            "v": 1,
            "workspace_id": workspace_id,
            "principal_id": principal_id,
            "delivery_class": delivery_class,
            "status": status,
        }
        if not isinstance(payload, Mapping) or any(payload.get(k) != v for k, v in expected.items()):
            raise ValueError("notification outbox cursor does not match request")
        created_at_us = payload.get("created_at_us")
        job_id = payload.get("job_id")
        fingerprint = payload.get("filter_fingerprint")
        if (
            type(created_at_us) is not int
            or not isinstance(job_id, str)
            or not job_id
            or not isinstance(fingerprint, str)
            or not fingerprint
        ):
            raise ValueError("notification outbox cursor is invalid")
        try:
            from importlib import import_module

            jobs_module = import_module("kogwistar.engine_core.jobs")
            cursor_type = cast(_JobQueueCursorFactory, jobs_module.JobQueueCursor)
        except ImportError as exc:
            raise NotificationOutboxUnavailableError(
                "notification outbox cursor requires a newer Kogwistar core"
            ) from exc
        return cursor_type(
            created_at_us=created_at_us,
            job_id=job_id,
            filter_fingerprint=fingerprint,
        )


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{name} must not contain control characters")
    return value.strip()


def _bounded_text(value: str, max_chars: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


__all__ = [
    "NotificationOutboxDigestItem",
    "NotificationOutboxEntry",
    "NotificationOutboxPage",
    "NotificationOutboxReader",
    "NotificationOutboxUnavailableError",
]
