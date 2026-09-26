"""Durable, credential-free email connector bindings."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol


class EmailBindingConflict(RuntimeError):
    """Raised when a connector binding update loses its revision race."""


class EmailBindingNotFound(KeyError):
    """Raised when a requested connector binding does not exist."""


_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _required(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _credential_ref(value: str) -> str:
    value = _required(value, "credential_ref")
    if len(value) > 512:
        raise ValueError("credential_ref is too long")
    if _SCHEME.match(value) or value.startswith(("/", "\\", "~")):
        raise ValueError("credential_ref must be an opaque reference, not a URL or path")
    return value


@dataclass(frozen=True, slots=True)
class EmailConnectorBinding:
    """Workspace-owned connector metadata; no secret value is persisted."""

    tenant_id: str
    workspace_id: str
    connector_id: str
    account_principal_id: str
    mailbox_id: str
    credential_ref: str
    folder_id: str | None = None
    enabled: bool = True
    revision: int = 0

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "workspace_id",
            "connector_id",
            "account_principal_id",
            "mailbox_id",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "credential_ref", _credential_ref(self.credential_ref))
        if self.folder_id is not None:
            object.__setattr__(self, "folder_id", _required(self.folder_id, "folder_id"))
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")

    @property
    def binding_id(self) -> str:
        return self.connector_id

    @property
    def stream_id(self) -> str:
        payload = {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "connector_id": self.connector_id,
            "account_principal_id": self.account_principal_id,
            "mailbox_id": self.mailbox_id,
            "folder_id": self.folder_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def namespace(self) -> str:
        return f"mail:{self.stream_id}"


class EmailConnectorBindingStore(Protocol):
    def get(self, *, workspace_id: str, connector_id: str) -> EmailConnectorBinding | None: ...

    def put(
        self,
        binding: EmailConnectorBinding,
        *,
        expected_revision: int | None = None,
    ) -> EmailConnectorBinding: ...

    def list(self, *, workspace_id: str) -> tuple[EmailConnectorBinding, ...]: ...


class InMemoryEmailConnectorBindingStore:
    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], EmailConnectorBinding] = {}
        self._lock = threading.Lock()

    def get(self, *, workspace_id: str, connector_id: str) -> EmailConnectorBinding | None:
        with self._lock:
            return self._bindings.get((workspace_id, connector_id))

    def put(
        self,
        binding: EmailConnectorBinding,
        *,
        expected_revision: int | None = None,
    ) -> EmailConnectorBinding:
        key = (binding.workspace_id, binding.connector_id)
        with self._lock:
            existing = self._bindings.get(key)
            _check_revision(existing, expected_revision)
            stored = replace(binding, revision=0 if existing is None else existing.revision + 1)
            self._bindings[key] = stored
            return stored

    def list(self, *, workspace_id: str) -> tuple[EmailConnectorBinding, ...]:
        with self._lock:
            return tuple(
                binding
                for (stored_workspace, _), binding in sorted(self._bindings.items())
                if stored_workspace == workspace_id
            )


class SQLiteEmailConnectorBindingStore:
    """SQLite binding store that deliberately has no secret-value column."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_connector_bindings (
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    account_principal_id TEXT NOT NULL,
                    mailbox_id TEXT NOT NULL,
                    credential_ref TEXT NOT NULL,
                    folder_id TEXT,
                    enabled INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, connector_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, *, workspace_id: str, connector_id: str) -> EmailConnectorBinding | None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT tenant_id, workspace_id, connector_id, account_principal_id, "
                "mailbox_id, credential_ref, folder_id, enabled, revision "
                "FROM email_connector_bindings WHERE workspace_id = ? AND connector_id = ?",
                (workspace_id, connector_id),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        return None if row is None else _binding_from_row(row)

    def put(
        self,
        binding: EmailConnectorBinding,
        *,
        expected_revision: int | None = None,
    ) -> EmailConnectorBinding:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "SELECT revision FROM email_connector_bindings "
                "WHERE workspace_id = ? AND connector_id = ?",
                (binding.workspace_id, binding.connector_id),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
            existing_revision = None if row is None else int(row[0])
            _check_revision(existing_revision, expected_revision)
            stored = replace(binding, revision=0 if row is None else existing_revision + 1)
            connection.execute(
                "INSERT INTO email_connector_bindings ("
                "tenant_id, workspace_id, connector_id, account_principal_id, mailbox_id, "
                "credential_ref, folder_id, enabled, revision"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, connector_id) DO UPDATE SET "
                "tenant_id=excluded.tenant_id, account_principal_id=excluded.account_principal_id, "
                "mailbox_id=excluded.mailbox_id, credential_ref=excluded.credential_ref, "
                "folder_id=excluded.folder_id, enabled=excluded.enabled, revision=excluded.revision",
                (
                    stored.tenant_id,
                    stored.workspace_id,
                    stored.connector_id,
                    stored.account_principal_id,
                    stored.mailbox_id,
                    stored.credential_ref,
                    stored.folder_id,
                    int(stored.enabled),
                    stored.revision,
                ),
            )
            connection.commit()
            return stored
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list(self, *, workspace_id: str) -> tuple[EmailConnectorBinding, ...]:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT tenant_id, workspace_id, connector_id, account_principal_id, "
                "mailbox_id, credential_ref, folder_id, enabled, revision "
                "FROM email_connector_bindings WHERE workspace_id = ? "
                "ORDER BY connector_id",
                (workspace_id,),
            )
            try:
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(_binding_from_row(row) for row in rows)


def _check_revision(
    existing: EmailConnectorBinding | int | None,
    expected_revision: int | None,
) -> None:
    existing_revision = (
        None
        if existing is None
        else existing.revision if isinstance(existing, EmailConnectorBinding) else existing
    )
    if existing_revision is None:
        if expected_revision not in (None, 0):
            raise EmailBindingConflict("connector binding does not exist at expected revision")
        return
    if expected_revision is None or expected_revision != existing_revision:
        raise EmailBindingConflict(
            f"connector binding revision conflict: expected {expected_revision}, "
            f"current {existing_revision}"
        )


def _binding_from_row(row: Iterable[object]) -> EmailConnectorBinding:
    values = tuple(row)
    return EmailConnectorBinding(
        tenant_id=str(values[0]),
        workspace_id=str(values[1]),
        connector_id=str(values[2]),
        account_principal_id=str(values[3]),
        mailbox_id=str(values[4]),
        credential_ref=str(values[5]),
        folder_id=None if values[6] is None else str(values[6]),
        enabled=bool(values[7]),
        revision=int(values[8]),
    )
