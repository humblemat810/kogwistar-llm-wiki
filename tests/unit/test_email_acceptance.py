from __future__ import annotations

import hashlib

import pytest

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.email import (
    EmailEvidenceRecord,
    EmailIngestRequest,
    EmailProposalMaterializer,
    InMemoryEmailEvidenceStore,
)
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.workbench.workbench_api import WorkbenchApi

pytestmark = pytest.mark.usefixtures("install_fake_email_plugin")

RAW_ACCEPTANCE_EMAIL = b"immutable email bytes"


def _record() -> EmailEvidenceRecord:
    return EmailEvidenceRecord(
        workspace_id="w",
        stream_id="stream-a",
        source_key="uid:1",
        source_revision_id="revision-1",
        content_sha256=hashlib.sha256(RAW_ACCEPTANCE_EMAIL).hexdigest(),
        parsed_payload={},
        mapping_payload={
            "mapping_id": "mapping-1",
            "ontology": {
                "ontology_id": "email",
                "version": "1.0.0",
                "content_sha256": "b" * 64,
                "schema_version": 1,
            },
            "entities": [
                {"entity_id": "message", "class_id": "EmailMessage", "properties": {"subject": ["Hello"]}},
                {"entity_id": "person", "class_id": "EmailAddress", "properties": {"address": ["a@example.test"]}},
            ],
            "relations": [
                {
                    "relation_id": "message_exchange",
                    "subject_id": "message",
                    "target_ids": ["person"],
                    "roles": {
                        "message": ["message"],
                        "sender": ["person"],
                        "recipient": [],
                    },
                }
            ],
        },
        blob_ref="sha256:" + hashlib.sha256(RAW_ACCEPTANCE_EMAIL).hexdigest(),
        source_document_id="email-source:1",
    )


def test_email_mapping_materializes_only_provenance_grounded_operations() -> None:
    proposal = EmailProposalMaterializer().build_patch(
        record=_record(),
        source_document_id="email-source:1",
    )

    assert len(proposal.patch.operations) == 3
    assert all(operation.provenance is not None for operation in proposal.patch.operations)
    assert all(
        operation.provenance.source_document_id == "email-source:1"
        for operation in proposal.patch.operations
        if operation.provenance is not None
    )
    assert proposal.patch.operations[-1].from_node_id.startswith("email:")


def test_email_mapping_requires_explicit_confirmation() -> None:
    proposal = EmailProposalMaterializer().build_patch(
        record=_record(),
        source_document_id="email-source:1",
    )
    assert EmailProposalMaterializer().accept(None, proposal, confirmed=False) is None  # type: ignore[arg-type]


def test_email_mapping_acceptance_uses_existing_scoped_patch_fence() -> None:
    engines = build_in_memory_namespace_engines()
    try:
        proposal = EmailProposalMaterializer().build_patch(
            record=_record(),
            source_document_id="email-source:1",
        )
        result = EmailProposalMaterializer().accept(engines, proposal, confirmed=True)
        assert result is not None
        assert result.status.value == "applied"
        assert result.applied_count == 3
        with _temporary_namespace(engines.kg, WorkspaceNamespaces("w").curated_kg_space):
            nodes = engines.kg.read.get_nodes()
            assert {node.label for node in nodes} >= {"EmailMessage", "EmailAddress"}
            assert len(engines.kg.read.get_edges()) == 1
    finally:
        engines.close()


def test_workbench_email_acceptance_is_explicit_and_idempotent(pipeline) -> None:
    store = InMemoryEmailEvidenceStore()
    record = _record()
    request = EmailIngestRequest(
        workspace_id=record.workspace_id,
        stream_id=record.stream_id,
        source_key=record.source_key,
            raw_bytes=RAW_ACCEPTANCE_EMAIL,
        source_revision_id=record.source_revision_id,
    )
    store.put(request, record)
    api = WorkbenchApi(
        pipeline,
        email_evidence_store=store,
        email_authorize_stream=lambda _workspace, _stream: True,
    )
    proposed = api.accept_email_mapping(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        source_document_id="email-source:1",
        confirmed=False,
    )
    assert proposed["status"] == "confirmation_required"

    accepted = api.accept_email_mapping(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        source_document_id="email-source:1",
        confirmed=True,
    )
    assert accepted["status"] == "applied"
    repeated = api.accept_email_mapping(
        workspace_id="w",
        stream_id="stream-a",
        source_revision_id="revision-1",
        source_document_id="email-source:1",
        confirmed=True,
    )
    assert repeated["status"] == "accepted"
    assert repeated["idempotent"] is True


def test_email_acceptance_rejects_source_document_mismatch_before_mutation(pipeline) -> None:
    store = InMemoryEmailEvidenceStore()
    record = _record()
    request = EmailIngestRequest(
        workspace_id=record.workspace_id,
        stream_id=record.stream_id,
        source_key=record.source_key,
            raw_bytes=RAW_ACCEPTANCE_EMAIL,
        source_revision_id=record.source_revision_id,
    )
    store.put(request, record)
    api = WorkbenchApi(
        pipeline,
        email_evidence_store=store,
        email_authorize_stream=lambda _workspace, _stream: True,
    )

    with pytest.raises(ValueError, match="does not match immutable email evidence"):
        api.accept_email_mapping(
            workspace_id="w",
            stream_id="stream-a",
            source_revision_id="revision-1",
            source_document_id="attacker-selected-document",
            confirmed=True,
        )
