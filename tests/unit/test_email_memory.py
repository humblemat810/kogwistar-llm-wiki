from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from kogwistar_llm_wiki.codex.codex_memory import CodexMemoryService
from kogwistar_llm_wiki.email import (
    EmailEvidenceRecord,
    EmailIngestRequest,
    EmailMemoryPromotionService,
    InMemoryEmailEvidenceStore,
    InMemoryEmailReviewStateStore,
)
from kogwistar_llm_wiki.email.review import EmailReviewState
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
from kogwistar_llm_wiki.workbench.workbench_api import WorkbenchApi


def _record() -> EmailEvidenceRecord:
    digest = hashlib.sha256(b"raw email").hexdigest()
    return EmailEvidenceRecord(
        workspace_id="w",
        stream_id="stream-a",
        source_key="uid:1",
        source_revision_id="revision-1",
        content_sha256=digest,
        parsed_payload={"subject": "Project update"},
        mapping_payload={
            "mapping_id": "mapping-1",
            "ontology": {
                "ontology_id": "email",
                "version": "1.0.0",
                "content_sha256": "b" * 64,
                "schema_version": 1,
            },
            "entities": [],
            "relations": [],
        },
        blob_ref=f"sha256:{digest}",
        source_document_id="email-source:authoritative",
    )


def _service(namespace_engines):
    evidence = InMemoryEmailEvidenceStore()
    record = _record()
    evidence.put(
        EmailIngestRequest(
            workspace_id=record.workspace_id,
            stream_id=record.stream_id,
            source_key=record.source_key,
            raw_bytes=b"raw email",
            source_revision_id=record.source_revision_id,
        ),
        record,
    )
    reviews = InMemoryEmailReviewStateStore()
    reviews.put(
        EmailReviewState(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-1",
            mapping_id="mapping-1",
            source_document_id="email-source:authoritative",
            status="accepted",
            patch_id="email-patch:mapping-1",
            result={"status": "applied"},
            updated_at_ms=1,
        )
    )
    return EmailMemoryPromotionService(
        evidence_store=evidence,
        review_store=reviews,
        memory_service=CodexMemoryService(namespace_engines, enabled=True),
        authorize_stream=lambda _workspace, _stream: True,
    )


def test_email_memory_promotion_requires_acceptance_and_keeps_exact_evidence(namespace_engines):
    service = _service(namespace_engines)
    promotion = service.build(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        statement="The project update says the delivery remains on schedule.",
    )

    assert promotion.source_document_id == "email-source:authoritative"
    assert promotion.descriptor_ids == ()
    assert promotion.payload["metadata"]["ontology"] == {
        "ontology_id": "email",
        "version": "1.0.0",
        "content_sha256": "b" * 64,
        "schema_version": 1,
    }
    assert promotion.payload["metadata"]["acl"] == {
        "workspace_id": "w",
        "stream_ids": ["stream-a"],
    }
    assert promotion.payload["evidence"] == [
        {
            "kind": "source_span",
            "source_document_id": "email-source:authoritative",
            "content_sha256": hashlib.sha256(b"raw email").hexdigest(),
            "locator": "email://stream-a/revision-1",
            "excerpt": "Subject: Project update",
        }
    ]
    assert service.promote(promotion, confirmed=False) is None
    captured = service.promote(promotion, confirmed=True)
    assert captured is not None
    assert captured["status"] == "captured"
    repeated = service.promote(promotion, confirmed=True)
    assert repeated is not None
    assert repeated["records"][0]["persisted"] is False


def test_email_memory_promotion_rejects_unpinned_ontology(namespace_engines):
    service = _service(namespace_engines)
    record = replace(
        _record(),
        source_revision_id="revision-no-ontology",
        mapping_payload={"mapping_id": "mapping-1", "entities": [], "relations": []},
    )
    service.evidence_store.put(
        EmailIngestRequest(
            workspace_id=record.workspace_id,
            stream_id=record.stream_id,
            source_key=record.source_key,
            raw_bytes=b"raw email",
            source_revision_id=record.source_revision_id,
        ),
        record,
    )
    service.review_store.put(
        EmailReviewState(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-no-ontology",
            mapping_id="mapping-1",
            source_document_id="email-source:authoritative",
            status="accepted",
            updated_at_ms=1,
        )
    )
    with pytest.raises(ValueError, match="pinned ontology identity"):
        service.build(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-no-ontology",
            statement="This must not be promoted",
        )


def test_email_memory_promotion_rejects_unaccepted_mapping(namespace_engines):
    service = _service(namespace_engines)
    service.review_store.put(
        EmailReviewState(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-1",
            mapping_id="mapping-1",
            source_document_id="email-source:authoritative",
            status="pending",
            updated_at_ms=1,
        )
    )

    with pytest.raises(ValueError, match="must be accepted"):
        service.build(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-1",
            statement="Do not promote this yet",
        )


def test_email_memory_promotion_intersects_acl_across_multiple_revisions(namespace_engines):
    service = _service(namespace_engines)
    second = replace(
        _record(),
        stream_id="stream-b",
        source_key="uid:2",
        source_revision_id="revision-2",
        source_document_id="email-source:second",
        content_sha256=hashlib.sha256(b"second raw email").hexdigest(),
        blob_ref="sha256:" + hashlib.sha256(b"second raw email").hexdigest(),
        mapping_payload={
            **_record().mapping_payload,
            "mapping_id": "mapping-2",
        },
    )
    service.evidence_store.put(
        EmailIngestRequest(
            workspace_id=second.workspace_id,
            stream_id=second.stream_id,
            source_key=second.source_key,
            raw_bytes=b"second raw email",
            source_revision_id=second.source_revision_id,
        ),
        second,
    )
    service.review_store.put(
        EmailReviewState(
            workspace_id="w",
            stream_id="stream-b",
            source_revision_id="revision-2",
            mapping_id="mapping-2",
            source_document_id="email-source:second",
            status="accepted",
            updated_at_ms=2,
        )
    )

    promotion = service.build_from_revisions(
        workspace_id="w",
        source_revision_ids=("revision-1", "revision-2"),
        expected_stream_ids=("stream-a", "stream-b"),
        statement="The two accepted messages describe the same delivery update.",
    )

    assert promotion.source_document_ids == (
        "email-source:authoritative",
        "email-source:second",
    )
    assert promotion.source_stream_ids == ("stream-a", "stream-b")
    assert promotion.payload["metadata"]["source_stream_ids"] == ["stream-a", "stream-b"]
    assert promotion.payload["metadata"]["acl"] == {
        "workspace_id": "w",
        "stream_ids": ["stream-a", "stream-b"],
    }
    assert len(promotion.payload["evidence"]) == 2

    api = WorkbenchApi(
        IngestPipeline(namespace_engines),
        email_evidence_store=service.evidence_store,
        email_review_store=service.review_store,
        email_authorize_stream=service.authorize_stream,
    )
    try:
        api_result = api.propose_email_memory(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-1",
            stream_ids=("stream-a", "stream-b"),
            source_revision_ids=("revision-1", "revision-2"),
            statement="The two accepted messages describe the same delivery update.",
        )
    finally:
        api.close()
    assert api_result["source_document_ids"] == [
        "email-source:authoritative",
        "email-source:second",
    ]


def test_email_memory_promotion_rejects_unauthorized_multi_source_revision(namespace_engines):
    service = _service(namespace_engines)
    second = replace(
        _record(),
        stream_id="stream-b",
        source_key="uid:2",
        source_revision_id="revision-2",
        source_document_id="email-source:second",
        content_sha256=hashlib.sha256(b"second raw email").hexdigest(),
        blob_ref="sha256:" + hashlib.sha256(b"second raw email").hexdigest(),
        mapping_payload={**_record().mapping_payload, "mapping_id": "mapping-2"},
    )
    service.evidence_store.put(
        EmailIngestRequest(
            workspace_id=second.workspace_id,
            stream_id=second.stream_id,
            source_key=second.source_key,
            raw_bytes=b"second raw email",
            source_revision_id=second.source_revision_id,
        ),
        second,
    )
    service.review_store.put(
        EmailReviewState(
            workspace_id="w",
            stream_id="stream-b",
            source_revision_id="revision-2",
            mapping_id="mapping-2",
            source_document_id="email-source:second",
            status="accepted",
            updated_at_ms=2,
        )
    )
    service.authorize_stream = lambda _workspace, stream: stream == "stream-a"

    with pytest.raises(PermissionError, match="not authorized"):
        service.build_from_revisions(
            workspace_id="w",
            source_revision_ids=("revision-1", "revision-2"),
            expected_stream_ids=("stream-a", "stream-b"),
            statement="This must not cross stream ACLs",
        )


def test_workbench_email_memory_promotion_is_explicit_and_idempotent(pipeline, monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_CODEX_MEMORY_ENABLED", "true")
    evidence = InMemoryEmailEvidenceStore()
    record = _record()
    evidence.put(
        EmailIngestRequest(
            workspace_id=record.workspace_id,
            stream_id=record.stream_id,
            source_key=record.source_key,
            raw_bytes=b"raw email",
            source_revision_id=record.source_revision_id,
        ),
        record,
    )
    reviews = InMemoryEmailReviewStateStore()
    reviews.put(
        EmailReviewState(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-1",
            mapping_id="mapping-1",
            source_document_id="email-source:authoritative",
            status="accepted",
            patch_id="email-patch:mapping-1",
            result={"status": "applied"},
            updated_at_ms=1,
        )
    )
    api = WorkbenchApi(
        pipeline,
        email_evidence_store=evidence,
        email_review_store=reviews,
    )

    proposal = api.propose_email_memory(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        statement="The project update says delivery remains on schedule.",
    )
    assert proposal["status"] == "proposed"
    confirmation = api.promote_email_memory(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        statement="The project update says delivery remains on schedule.",
        confirmed=False,
    )
    assert confirmation["status"] == "confirmation_required"
    captured = api.promote_email_memory(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        statement="The project update says delivery remains on schedule.",
        confirmed=True,
    )
    assert captured["status"] == "captured"
    repeated = api.promote_email_memory(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        statement="The project update says delivery remains on schedule.",
        confirmed=True,
    )
    assert repeated["result"]["records"][0]["persisted"] is False
