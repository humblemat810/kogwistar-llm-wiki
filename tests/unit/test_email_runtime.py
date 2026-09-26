from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from kogwistar_llm_wiki.email import (
    EmailEvidenceRecord,
    EmailIngestRequest,
    EmailRuntime,
    EmailViewer,
    InMemoryEmailEvidenceStore,
    InMemoryEmailReviewStateStore,
    SQLiteEmailEvidenceStore,
    SQLiteEmailReviewStateStore,
)
from kogwistar_llm_wiki.email.review import EmailReviewState

RAW_EMAIL = (
    b"From: Alice <alice@example.test>\r\n"
    b"To: Bob <bob@example.test>\r\n"
    b"Subject: Project update\r\n"
    b"Message-ID: <message-1@example.test>\r\n"
    b"Date: Tue, 22 Sep 2026 10:00:00 +0000\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"The project is on schedule.\r\n"
)


def test_email_runtime_persists_immutable_evidence_and_pending_mapping(pipeline) -> None:
    store = InMemoryEmailEvidenceStore()
    runtime = EmailRuntime(pipeline=pipeline, store=store)
    request = EmailIngestRequest(
        workspace_id="email-workspace",
        stream_id="mail-stream-a",
        source_key="uid:1",
        raw_bytes=RAW_EMAIL,
        source_revision_id="rfc822:revision-1",
    )

    result = runtime.ingest(request)

    assert result.status == "proposals_pending"
    assert result.source_document_id.startswith("email-source:")
    assert result.derivation_id.startswith("email-derivation:")
    stored = store.get(
        workspace_id=request.workspace_id,
        source_revision_id=request.source_revision_id,
    )
    assert stored is not None
    assert stored.content_sha256
    assert stored.mapping_payload["mapping_id"] == result.mapping_id
    assert store.raw_bytes(
        workspace_id=request.workspace_id,
        source_revision_id=request.source_revision_id,
    ) == RAW_EMAIL


def test_email_viewer_checks_workspace_stream_scope_and_escapes_html(pipeline) -> None:
    store = InMemoryEmailEvidenceStore()
    runtime = EmailRuntime(pipeline=pipeline, store=store)
    request = EmailIngestRequest(
        workspace_id="viewer-workspace",
        stream_id="mail-stream-a",
        source_key="uid:2",
        raw_bytes=RAW_EMAIL.replace(b"Project update", b"<unsafe>"),
        source_revision_id="rfc822:revision-2",
    )
    runtime.ingest(request)
    review_store = InMemoryEmailReviewStateStore()
    viewer = EmailViewer(
        store,
        authorize_stream=lambda workspace, stream: workspace == "viewer-workspace",
        review_store=review_store,
    )

    result = viewer.get(
        workspace_id=request.workspace_id,
        stream_id=request.stream_id,
        source_revision_id=request.source_revision_id,
    )

    assert result["status"] == "ok"
    assert "&lt;unsafe&gt;" in str(result["safe_html_preview"])
    assert result["mapping_status"] == "pending"
    review_store.put(
        EmailReviewState(
            workspace_id=request.workspace_id,
            stream_id=request.stream_id,
            source_revision_id=request.source_revision_id,
            mapping_id=str(result["mapping_id"]),
            source_document_id="email-source:1",
            status="accepted",
            patch_id="email-patch:1",
            result={"status": "applied"},
            updated_at_ms=1,
        )
    )
    accepted = viewer.get(
        workspace_id=request.workspace_id,
        stream_id=request.stream_id,
        source_revision_id=request.source_revision_id,
    )
    assert accepted["mapping_status"] == "accepted"
    assert accepted["review"]["patch_id"] == "email-patch:1"
    with pytest.raises(PermissionError, match="not authorized"):
        viewer.get(
            workspace_id="other-workspace",
            stream_id=request.stream_id,
            source_revision_id=request.source_revision_id,
        )


def test_email_evidence_store_rejects_identity_or_digest_mismatch(pipeline) -> None:
    store = InMemoryEmailEvidenceStore()
    runtime = EmailRuntime(pipeline=pipeline, store=store)
    request = EmailIngestRequest(
        workspace_id="evidence-workspace",
        stream_id="mail-stream-a",
        source_key="uid:3",
        raw_bytes=RAW_EMAIL,
        source_revision_id="rfc822:revision-3",
    )
    runtime.ingest(request)
    record = store.get(
        workspace_id=request.workspace_id,
        source_revision_id=request.source_revision_id,
    )
    assert record is not None

    with pytest.raises(ValueError, match="identity"):
        store.put(request, replace(record, workspace_id="other-workspace"))
    with pytest.raises(ValueError, match="digest"):
        store.put(request, replace(record, content_sha256="0" * 64))


def test_email_runtime_rejects_unauthorized_stream_without_parsing(pipeline) -> None:
    def authorize(_workspace: str, _stream: str) -> bool:
        return False

    runtime = EmailRuntime(
        pipeline=pipeline,
        store=InMemoryEmailEvidenceStore(),
        authorize_stream=authorize,
    )
    with pytest.raises(PermissionError, match="not authorized"):
        runtime.ingest(
            EmailIngestRequest(
                workspace_id="email-workspace",
                stream_id="mail-stream-a",
                source_key="uid:3",
                raw_bytes=RAW_EMAIL,
                source_revision_id="rfc822:revision-3",
            )
        )


def test_email_review_state_stores_are_durable_and_consistent(tmp_path) -> None:
    state = EmailReviewState(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        mapping_id="mapping-1",
        source_document_id="email-source:1",
        status="accepted",
        patch_id="email-patch:mapping-1",
        result={"status": "applied", "applied_count": 2},
        updated_at_ms=123,
    )
    store = SQLiteEmailReviewStateStore(tmp_path / "review.sqlite")
    store.put(state)
    assert store.get(
        workspace_id="w",
        source_revision_id="revision-1",
        mapping_id="mapping-1",
    ) == state

    memory_store = InMemoryEmailReviewStateStore()
    memory_store.put(state)
    assert memory_store.get(
        workspace_id="w",
        source_revision_id="revision-1",
        mapping_id="mapping-1",
    ) == state


def test_sqlite_email_evidence_persists_authoritative_source_binding(tmp_path: Path) -> None:
    store = SQLiteEmailEvidenceStore(tmp_path / "email.sqlite3")
    request = EmailIngestRequest(
        workspace_id="w",
        stream_id="stream-a",
        source_key="uid:1",
        raw_bytes=b"raw",
        source_revision_id="revision-1",
    )
    record = EmailEvidenceRecord(
        workspace_id="w",
        stream_id="stream-a",
        source_key="uid:1",
        source_revision_id="revision-1",
        content_sha256=hashlib.sha256(b"raw").hexdigest(),
        parsed_payload={},
        mapping_payload={"mapping_id": "mapping-1", "entities": [], "relations": []},
        blob_ref="sha256:" + hashlib.sha256(b"raw").hexdigest(),
        source_document_id="email-source:authoritative",
    )
    store.put(request, record)

    assert store.get(workspace_id="w", source_revision_id="revision-1") == record


def test_sqlite_email_evidence_migrates_legacy_schema_without_inventing_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-email.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE email_evidence ("
            "workspace_id TEXT NOT NULL, stream_id TEXT NOT NULL, "
            "source_key TEXT NOT NULL, source_revision_id TEXT NOT NULL, "
            "content_sha256 TEXT NOT NULL, raw_bytes BLOB NOT NULL, "
            "parsed_json TEXT NOT NULL, mapping_json TEXT NOT NULL, "
            "blob_ref TEXT NOT NULL, PRIMARY KEY (workspace_id, source_revision_id)"
            ")"
        )
        connection.execute(
            "INSERT INTO email_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "w",
                "stream-a",
                "uid:legacy",
                "revision-legacy",
                "a" * 64,
                b"raw",
                json.dumps({}),
                json.dumps({"mapping_id": "mapping-legacy", "entities": [], "relations": []}),
                "sha256:" + "a" * 64,
            ),
        )

    store = SQLiteEmailEvidenceStore(path)
    record = store.get(workspace_id="w", source_revision_id="revision-legacy")

    assert record is not None
    assert record.source_document_id is None
