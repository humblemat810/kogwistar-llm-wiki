from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kogwistar_llm_wiki.email import (
    EmailBindingConflict,
    EmailConnectorBinding,
    InMemoryEmailConnectorBindingStore,
    InMemoryEmailSyncLeaseStore,
    SQLiteEmailConnectorBindingStore,
    SQLiteEmailSyncLeaseStore,
)
from kogwistar_llm_wiki.email.leases import EmailSyncLeaseConflict, EmailSyncLeaseLost


def _binding(*, workspace_id: str = "workspace-a", enabled: bool = True) -> EmailConnectorBinding:
    return EmailConnectorBinding(
        tenant_id="tenant-a",
        workspace_id=workspace_id,
        connector_id="connector-a",
        account_principal_id="account-a",
        mailbox_id="mailbox-a",
        credential_ref="secret/email/connector-a",
        folder_id="INBOX",
        enabled=enabled,
    )


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_binding_store_is_cas_protected_and_workspace_scoped(kind: str, tmp_path: Path) -> None:
    store = (
        InMemoryEmailConnectorBindingStore()
        if kind == "memory"
        else SQLiteEmailConnectorBindingStore(tmp_path / "bindings.sqlite3")
    )
    first = store.put(_binding())
    assert first.revision == 0
    assert store.get(workspace_id="other", connector_id="connector-a") is None

    updated = store.put(
        _binding(enabled=False),
        expected_revision=first.revision,
    )
    assert updated.revision == 1
    assert updated.enabled is False
    with pytest.raises(EmailBindingConflict):
        store.put(_binding(), expected_revision=first.revision)


def test_binding_rejects_credential_urls_and_paths() -> None:
    for credential_ref in ("https://vault.example/ref", "/tmp/secret"):
        with pytest.raises(ValueError, match="opaque reference"):
            EmailConnectorBinding(
                tenant_id="tenant",
                workspace_id="workspace",
                connector_id="connector",
                account_principal_id="account",
                mailbox_id="mailbox",
                credential_ref=credential_ref,
            )
    assert _binding().credential_ref == "secret/email/connector-a"


def test_sqlite_binding_schema_has_reference_but_no_secret_column(tmp_path: Path) -> None:
    path = tmp_path / "bindings.sqlite3"
    store = SQLiteEmailConnectorBindingStore(path)
    store.put(_binding())

    with sqlite3.connect(path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(email_connector_bindings)")}
        row = connection.execute(
            "SELECT credential_ref FROM email_connector_bindings"
        ).fetchone()
    assert "credential_ref" in columns
    assert "password" not in columns
    assert row == ("secret/email/connector-a",)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_lease_contention_expiry_and_renewal(kind: str, tmp_path: Path) -> None:
    store = (
        InMemoryEmailSyncLeaseStore()
        if kind == "memory"
        else SQLiteEmailSyncLeaseStore(tmp_path / "leases.sqlite3")
    )
    lease = store.claim(
        workspace_id="workspace-a",
        connector_id="connector-a",
        stream_id="stream-a",
        owner_id="worker-a",
        now_ms=100,
        lease_duration_ms=100,
    )
    with pytest.raises(EmailSyncLeaseConflict):
        store.claim(
            workspace_id="workspace-a",
            connector_id="connector-a",
            stream_id="stream-a",
            owner_id="worker-b",
            now_ms=150,
            lease_duration_ms=100,
        )
    renewed = store.renew(lease, now_ms=150, lease_duration_ms=100)
    assert renewed.lease_token == lease.lease_token
    assert renewed.expires_at_ms == 250
    with pytest.raises(EmailSyncLeaseLost):
        store.renew(renewed, now_ms=250, lease_duration_ms=100)
    replacement = store.claim(
        workspace_id="workspace-a",
        connector_id="connector-a",
        stream_id="stream-a",
        owner_id="worker-b",
        now_ms=250,
        lease_duration_ms=100,
    )
    assert replacement.owner_id == "worker-b"
