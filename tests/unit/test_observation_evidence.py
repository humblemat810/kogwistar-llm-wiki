from __future__ import annotations

import hashlib
from types import SimpleNamespace

from kogwistar_llm_wiki.maintenance.maintenance_observation import (
    ObservationSubject,
    assess_observation_frame,
    build_observation_frame,
)
from kogwistar_llm_wiki.maintenance.observation_evidence import (
    build_authoritative_source_evidence,
)
from kogwistar_llm_wiki.maintenance.worker_observation import _redacted_frame_payload


class _ScopedRead:
    def __init__(self, owner: _ScopedEngine) -> None:
        self._e = owner

    def get_nodes(self, *, ids: list[str], limit: int) -> list[object]:
        if self._e.namespace != SOURCE_NAMESPACE:
            return []
        return [SOURCE_NODE] if SOURCE_NODE.id in ids and limit else []

    def get_edges(self, *, ids: list[str], limit: int) -> list[object]:
        return []

    def get_document(self, document_id: str) -> object:
        if self._e.namespace != SOURCE_NAMESPACE or document_id != DOCUMENT.id:
            raise LookupError("document unavailable")
        return DOCUMENT


class _ScopedEngine:
    def __init__(self) -> None:
        self.namespace = "unrelated-default-namespace"
        self.read = _ScopedRead(self)


WORKSPACE = "observation-evidence-test"
SOURCE_NAMESPACE = f"ws:{WORKSPACE}:g:source"
SOURCE_TEXT = "The authoritative source states that revenue grew 12 percent."
SOURCE_DIGEST = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()
DOCUMENT = SimpleNamespace(
    id="revision-doc-1",
    content=SOURCE_TEXT,
    metadata={
        "workspace_id": WORKSPACE,
        "logical_source_document_id": "logical-source-1",
        "source_revision_id": "revision-1",
        "revision_document_id": "revision-doc-1",
        "source_digest": SOURCE_DIGEST,
        "title": "Quarterly report",
    },
)
SOURCE_NODE = SimpleNamespace(
    id="source-node-1",
    label="Revenue increased",
    summary="Revenue grew during the quarter.",
    metadata={"workspace_id": WORKSPACE},
    iter_evidence=lambda: iter(
        [
            SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "doc_id": DOCUMENT.id,
                    "start_char": 0,
                    "end_char": 35,
                    "excerpt": SOURCE_TEXT[:35],
                }
            )
        ]
    ),
)


def _pointer(*, target_namespace: str = SOURCE_NAMESPACE) -> object:
    return SimpleNamespace(
        id="pointer-1",
        label="Revenue claim",
        summary="Revenue grew.",
        metadata={
            "workspace_id": WORKSPACE,
            "source_document_id": "logical-source-1",
            "target_namespace": target_namespace,
            "target_kind": "node",
            "target_id": SOURCE_NODE.id,
        },
    )


def test_source_pointer_resolves_in_source_namespace_and_verifies_pinned_span() -> None:
    engine = _ScopedEngine()

    record = build_authoritative_source_evidence(
        engine,
        _pointer(),
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
        expected_source_document_id="logical-source-1",
        expected_revision_id="revision-1",
        expected_revision_document_id=DOCUMENT.id,
        expected_source_digest=SOURCE_DIGEST,
    )

    assert record["source_evidence_status"] == "span_verified"
    assert record["source_excerpt"] == SOURCE_TEXT[:35]
    assert record["source_digest"] == SOURCE_DIGEST
    assert engine.namespace == "unrelated-default-namespace"


def test_source_pointer_uses_its_own_logical_source_pin_when_job_has_none() -> None:
    record = build_authoritative_source_evidence(
        _ScopedEngine(),
        _pointer(),
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
    )

    assert record["source_evidence_status"] == "span_verified"
    assert record["logical_source_document_id"] == "logical-source-1"


def test_out_of_scope_pointer_is_rejected_without_reading_target() -> None:
    record = build_authoritative_source_evidence(
        _ScopedEngine(),
        _pointer(target_namespace="ws:other:g:source"),
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
    )

    assert record["source_evidence_status"] == "source_pointer_out_of_scope"
    assert "source_excerpt" not in record


def test_source_digest_and_revision_mismatches_fail_closed() -> None:
    engine = _ScopedEngine()
    pointer = _pointer()

    wrong_digest = build_authoritative_source_evidence(
        engine,
        pointer,
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
        expected_source_digest="0" * 64,
    )
    wrong_revision = build_authoritative_source_evidence(
        engine,
        pointer,
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
        expected_revision_id="old-revision",
    )
    wrong_document = build_authoritative_source_evidence(
        engine,
        pointer,
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
        expected_revision_document_id="old-document",
    )

    assert wrong_digest["source_evidence_status"] == "pinned_source_digest_mismatch"
    assert wrong_revision["source_evidence_status"] == "source_revision_mismatch"
    assert wrong_document["source_evidence_status"] == "source_revision_document_mismatch"
    assert all("source_excerpt" not in item for item in (wrong_digest, wrong_revision, wrong_document))

    wrong_logical_source = build_authoritative_source_evidence(
        engine,
        pointer,
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
        expected_source_document_id="different-logical-source",
    )
    assert wrong_logical_source["source_evidence_status"] == "logical_source_document_mismatch"
    assert "source_excerpt" not in wrong_logical_source


def test_missing_workspace_or_inconsistent_document_digest_fails_closed(monkeypatch) -> None:
    engine = _ScopedEngine()
    no_workspace_document = SimpleNamespace(
        id=DOCUMENT.id,
        content=SOURCE_TEXT,
        metadata={"source_digest": SOURCE_DIGEST},
    )
    monkeypatch.setattr(engine.read, "get_document", lambda _document_id: no_workspace_document)
    missing_workspace = build_authoritative_source_evidence(
        engine,
        _pointer(),
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
    )

    corrupt_document = SimpleNamespace(
        id=DOCUMENT.id,
        content=SOURCE_TEXT + " modified",
        metadata={
            "workspace_id": WORKSPACE,
            "logical_source_document_id": "logical-source-1",
            "source_digest": SOURCE_DIGEST,
        },
    )
    monkeypatch.setattr(engine.read, "get_document", lambda _document_id: corrupt_document)
    digest_mismatch = build_authoritative_source_evidence(
        engine,
        _pointer(),
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
    )

    assert missing_workspace["source_evidence_status"] == "source_workspace_mismatch"
    assert digest_mismatch["source_evidence_status"] == "source_digest_mismatch"
    assert "source_excerpt" not in missing_workspace
    assert "source_excerpt" not in digest_mismatch


def test_unverifiable_legacy_source_is_not_exposed_as_evidence(monkeypatch) -> None:
    engine = _ScopedEngine()
    legacy_document = SimpleNamespace(
        id="revision-doc-1",
        content=SOURCE_TEXT,
        metadata={
            "workspace_id": WORKSPACE,
            "logical_source_document_id": "logical-source-1",
        },
    )
    monkeypatch.setattr(engine.read, "get_document", lambda _document_id: legacy_document)

    record = build_authoritative_source_evidence(
        engine,
        _pointer(),
        workspace_id=WORKSPACE,
        source_namespace=SOURCE_NAMESPACE,
    )

    assert record["source_evidence_status"] == "legacy_evidence_unavailable"
    assert "source_excerpt" not in record


def test_required_source_evidence_prevents_adequate_assessment() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id=WORKSPACE,
        namespace=f"ws:{WORKSPACE}:g:curated_kg",
    )
    frame = build_observation_frame(subject, source_evidence_required=True)

    assessment = assess_observation_frame(frame, critic_status="succeeded")

    assert assessment.verdict == "review_required"
    assert assessment.recommended_action == "request_human_review"
    assert any(item.code == "source_evidence_unavailable" for item in assessment.findings)


def test_verified_source_evidence_can_support_adequate_assessment() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id=WORKSPACE,
        namespace=f"ws:{WORKSPACE}:g:curated_kg",
        revision_id="revision-1",
        revision_document_id=DOCUMENT.id,
        source_digest=SOURCE_DIGEST,
    )
    frame = build_observation_frame(
        subject,
        source_context=[
            {
                "id": "source-node-1",
                "workspace_id": WORKSPACE,
                "namespace": SOURCE_NAMESPACE,
                "evidence_role": "authoritative_source",
                "source_evidence_status": "span_verified",
                "source_revision_id": "revision-1",
                "revision_document_id": DOCUMENT.id,
                "source_digest": SOURCE_DIGEST,
            }
        ],
        source_evidence_required=True,
    )

    assessment = assess_observation_frame(frame, critic_status="succeeded")

    assert assessment.verdict == "adequate"
    assert assessment.recommended_action == "none"


def test_stale_authoritative_evidence_is_filtered_before_assessment() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id=WORKSPACE,
        namespace=f"ws:{WORKSPACE}:g:curated_kg",
        revision_id="revision-current",
        revision_document_id="document-current",
        source_digest="a" * 64,
    )
    frame = build_observation_frame(
        subject,
        source_context=[
            {
                "id": "source-old",
                "workspace_id": WORKSPACE,
                "namespace": SOURCE_NAMESPACE,
                "evidence_role": "authoritative_source",
                "source_evidence_status": "span_verified",
                "source_revision_id": "revision-old",
                "revision_document_id": "document-old",
                "source_digest": "b" * 64,
            }
        ],
        source_evidence_required=True,
    )

    assessment = assess_observation_frame(frame, critic_status="succeeded")

    assert not frame.source_context
    assert assessment.verdict == "review_required"
    assert assessment.recommended_action == "request_human_review"


def test_frame_persistence_redacts_transient_source_excerpt() -> None:
    subject = ObservationSubject(
        kind="node",
        subject_id="node-1",
        workspace_id=WORKSPACE,
        namespace=f"ws:{WORKSPACE}:g:curated_kg",
    )
    frame = build_observation_frame(
        subject,
        source_context=[
            {
                "id": "source-node-1",
                "workspace_id": WORKSPACE,
                "namespace": SOURCE_NAMESPACE,
                "evidence_role": "authoritative_source",
                "source_evidence_status": "span_verified",
                "source_excerpt": "private source excerpt",
                "source_revision_id": "revision-1",
            }
        ],
        source_evidence_required=True,
    )

    payload = _redacted_frame_payload(frame)

    assert payload["source_context"][0]["source_evidence_status"] == "span_verified"
    assert "source_excerpt" not in payload["source_context"][0]
