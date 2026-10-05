"""Compose bounded notification event sources behind shared ACL checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import islice
from typing import Protocol

from .notification_digest import NotificationEvent


class AuthorizeNotificationSource(Protocol):
    """Authorize one notification source for a recipient."""

    def __call__(self, workspace_id: str, recipient_id: str, source_id: str, /) -> bool: ...


class NotificationSourceAdapter(Protocol):
    """Read source IDs and finalized-window events for one recipient."""

    def list_sources(self, workspace_id: str, recipient_id: str) -> Sequence[str]: ...

    def read_window(
        self,
        workspace_id: str,
        recipient_id: str,
        source_ids: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
        max_events: int,
    ) -> Sequence[NotificationEvent]: ...


class NotificationSourceCollection:
    """Combine trusted channel adapters without merging their authority.

    Source IDs must be globally unique across configured adapters. Enumeration
    is repeated before each window read so source ownership and ACL revocation
    are checked again immediately before adapter I/O.
    """

    def __init__(
        self,
        adapters: Mapping[str, NotificationSourceAdapter],
        *,
        authorize_source: AuthorizeNotificationSource,
        max_sources: int = 1000,
    ) -> None:
        if not isinstance(adapters, Mapping) or not adapters:
            raise ValueError("at least one notification source adapter is required")
        if not callable(authorize_source):
            raise TypeError("notification source authorizer is required")
        if type(max_sources) is not int or not 1 <= max_sources <= 10_000:
            raise ValueError("max_sources must be between 1 and 10000")
        configured = []
        for name, adapter in adapters.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("notification adapter names must be non-empty strings")
            if not callable(getattr(adapter, "list_sources", None)) or not callable(
                getattr(adapter, "read_window", None)
            ):
                raise TypeError(f"notification adapter {name!r} lacks required methods")
            configured.append((name.strip(), adapter))
        if len({name for name, _adapter in configured}) != len(configured):
            raise ValueError("notification adapter names must be unique")
        self.adapters = tuple(sorted(configured, key=lambda item: item[0]))
        self.authorize_source = authorize_source
        self.max_sources = max_sources

    def list_sources(self, workspace_id: str, recipient_id: str) -> tuple[str, ...]:
        sources = self._enumerate(workspace_id, recipient_id)
        for source_id in sources:
            if not self.authorize_source(workspace_id, recipient_id, source_id):
                raise PermissionError("notification source is not authorized")
        return sources

    def read_window(
        self,
        workspace_id: str,
        recipient_id: str,
        source_ids: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
        max_events: int,
    ) -> tuple[NotificationEvent, ...]:
        if type(max_events) is not int or max_events < 1:
            raise ValueError("max_events must be positive")
        if not isinstance(source_ids, tuple) or len(source_ids) > self.max_sources:
            raise ValueError("source_ids must be a bounded tuple")
        if any(not isinstance(source, str) or not source.strip() for source in source_ids):
            raise ValueError("source_ids must contain non-empty strings")
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source_ids must be unique")
        ownership = self._source_ownership(workspace_id, recipient_id)
        if set(source_ids) != set(ownership):
            raise RuntimeError("notification source set changed during window production")
        for source_id in source_ids:
            if not self.authorize_source(workspace_id, recipient_id, source_id):
                raise PermissionError("notification source is not authorized")

        events: list[NotificationEvent] = []
        for name, adapter in self.adapters:
            owned = tuple(sorted(source for source, owner in ownership.items() if owner == name))
            if not owned:
                continue
            batch = tuple(
                islice(
                    adapter.read_window(
                        workspace_id,
                        recipient_id,
                        owned,
                        window_start,
                        window_end,
                        max_events - len(events) + 1,
                    ),
                    max_events - len(events) + 1,
                )
            )
            if len(batch) > max_events - len(events):
                raise ValueError("notification event window exceeds configured bound")
            for event in batch:
                if not isinstance(event, NotificationEvent):
                    raise TypeError(f"notification adapter {name!r} returned an invalid event")
                if event.workspace_id != workspace_id:
                    raise ValueError(f"notification adapter {name!r} returned an out-of-scope event")
                if not self.authorize_source(workspace_id, recipient_id, event.source_id):
                    raise PermissionError("notification evidence source is not authorized")
                events.append(event)
        return tuple(events)

    def _source_ownership(self, workspace_id: str, recipient_id: str) -> dict[str, str]:
        ownership: dict[str, str] = {}
        for name, adapter in self.adapters:
            sources = tuple(islice(adapter.list_sources(workspace_id, recipient_id), self.max_sources + 1))
            if len(sources) > self.max_sources:
                raise ValueError(f"notification adapter {name!r} exceeds source bound")
            for source in sources:
                if not isinstance(source, str) or not source.strip():
                    raise ValueError(f"notification adapter {name!r} returned an invalid source ID")
                source_id = source.strip()
                if source_id in ownership:
                    raise ValueError("notification source IDs must be unique across adapters")
                ownership[source_id] = name
                if len(ownership) > self.max_sources:
                    raise ValueError("notification source set exceeds configured bound")
        return ownership

    def _enumerate(self, workspace_id: str, recipient_id: str) -> tuple[str, ...]:
        return tuple(sorted(self._source_ownership(workspace_id, recipient_id)))


__all__ = [
    "AuthorizeNotificationSource",
    "NotificationSourceAdapter",
    "NotificationSourceCollection",
]
