"""Atomic per-connector synchronization leases."""

from __future__ import annotations

import secrets
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class EmailSyncLeaseConflict(RuntimeError):
    """Raised when another owner currently holds a connector lease."""


class EmailSyncLeaseLost(RuntimeError):
    """Raised when a lease cannot be renewed by its owner."""


@dataclass(frozen=True, slots=True)
class EmailSyncLease:
    workspace_id: str
    connector_id: str
    stream_id: str
    owner_id: str
    lease_token: str
    expires_at_ms: int

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "connector_id",
            "stream_id",
            "owner_id",
            "lease_token",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if type(self.expires_at_ms) is not int or self.expires_at_ms < 0:
            raise ValueError("expires_at_ms must be a non-negative integer")


class EmailSyncLeaseStore(Protocol):
    def claim(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        stream_id: str,
        owner_id: str,
        now_ms: int,
        lease_duration_ms: int,
    ) -> EmailSyncLease: ...

    def renew(
        self,
        lease: EmailSyncLease,
        *,
        now_ms: int,
        lease_duration_ms: int,
    ) -> EmailSyncLease: ...

    def release(self, lease: EmailSyncLease) -> None: ...


def _duration(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("lease_duration_ms must be positive")
    return value


class InMemoryEmailSyncLeaseStore:
    def __init__(self) -> None:
        self._leases: dict[tuple[str, str], EmailSyncLease] = {}
        self._lock = threading.Lock()

    def claim(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        stream_id: str,
        owner_id: str,
        now_ms: int,
        lease_duration_ms: int,
    ) -> EmailSyncLease:
        lease_duration_ms = _duration(lease_duration_ms)
        key = (workspace_id, connector_id)
        with self._lock:
            current = self._leases.get(key)
            if current is not None and current.expires_at_ms > now_ms and current.owner_id != owner_id:
                raise EmailSyncLeaseConflict("email connector is already being synchronized")
            token = current.lease_token if current is not None and current.owner_id == owner_id else secrets.token_urlsafe(24)
            lease = EmailSyncLease(
                workspace_id=workspace_id,
                connector_id=connector_id,
                stream_id=stream_id,
                owner_id=owner_id,
                lease_token=token,
                expires_at_ms=now_ms + lease_duration_ms,
            )
            self._leases[key] = lease
            return lease

    def renew(
        self,
        lease: EmailSyncLease,
        *,
        now_ms: int,
        lease_duration_ms: int,
    ) -> EmailSyncLease:
        lease_duration_ms = _duration(lease_duration_ms)
        key = (lease.workspace_id, lease.connector_id)
        with self._lock:
            current = self._leases.get(key)
            if current != lease or current.expires_at_ms <= now_ms:
                raise EmailSyncLeaseLost("email connector lease is no longer owned")
            renewed = EmailSyncLease(
                workspace_id=lease.workspace_id,
                connector_id=lease.connector_id,
                stream_id=lease.stream_id,
                owner_id=lease.owner_id,
                lease_token=lease.lease_token,
                expires_at_ms=now_ms + lease_duration_ms,
            )
            self._leases[key] = renewed
            return renewed

    def release(self, lease: EmailSyncLease) -> None:
        key = (lease.workspace_id, lease.connector_id)
        with self._lock:
            if self._leases.get(key) == lease:
                self._leases.pop(key, None)


class SQLiteEmailSyncLeaseStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_sync_leases (
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, connector_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def claim(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        stream_id: str,
        owner_id: str,
        now_ms: int,
        lease_duration_ms: int,
    ) -> EmailSyncLease:
        lease_duration_ms = _duration(lease_duration_ms)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "SELECT stream_id, owner_id, lease_token, expires_at_ms "
                "FROM email_sync_leases WHERE workspace_id = ? AND connector_id = ?",
                (workspace_id, connector_id),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
            if row is not None and int(row[3]) > now_ms and str(row[1]) != owner_id:
                raise EmailSyncLeaseConflict("email connector is already being synchronized")
            token = str(row[2]) if row is not None and str(row[1]) == owner_id else secrets.token_urlsafe(24)
            lease = EmailSyncLease(
                workspace_id=workspace_id,
                connector_id=connector_id,
                stream_id=stream_id,
                owner_id=owner_id,
                lease_token=token,
                expires_at_ms=now_ms + lease_duration_ms,
            )
            connection.execute(
                "INSERT INTO email_sync_leases (workspace_id, connector_id, stream_id, owner_id, lease_token, expires_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, connector_id) DO UPDATE SET "
                "stream_id=excluded.stream_id, owner_id=excluded.owner_id, "
                "lease_token=excluded.lease_token, expires_at_ms=excluded.expires_at_ms",
                (
                    lease.workspace_id,
                    lease.connector_id,
                    lease.stream_id,
                    lease.owner_id,
                    lease.lease_token,
                    lease.expires_at_ms,
                ),
            )
            connection.commit()
            return lease
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew(
        self,
        lease: EmailSyncLease,
        *,
        now_ms: int,
        lease_duration_ms: int,
    ) -> EmailSyncLease:
        lease_duration_ms = _duration(lease_duration_ms)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "SELECT stream_id, owner_id, lease_token, expires_at_ms "
                "FROM email_sync_leases WHERE workspace_id = ? AND connector_id = ?",
                (lease.workspace_id, lease.connector_id),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
            if (
                row is None
                or str(row[1]) != lease.owner_id
                or str(row[2]) != lease.lease_token
                or int(row[3]) <= now_ms
            ):
                raise EmailSyncLeaseLost("email connector lease is no longer owned")
            renewed = EmailSyncLease(
                workspace_id=lease.workspace_id,
                connector_id=lease.connector_id,
                stream_id=lease.stream_id,
                owner_id=lease.owner_id,
                lease_token=lease.lease_token,
                expires_at_ms=now_ms + lease_duration_ms,
            )
            connection.execute(
                "UPDATE email_sync_leases SET expires_at_ms = ? "
                "WHERE workspace_id = ? AND connector_id = ?",
                (renewed.expires_at_ms, lease.workspace_id, lease.connector_id),
            )
            connection.commit()
            return renewed
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release(self, lease: EmailSyncLease) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "DELETE FROM email_sync_leases WHERE workspace_id = ? AND connector_id = ? "
                "AND owner_id = ? AND lease_token = ?",
                (lease.workspace_id, lease.connector_id, lease.owner_id, lease.lease_token),
            )
            connection.commit()
        finally:
            connection.close()
