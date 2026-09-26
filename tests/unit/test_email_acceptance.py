from __future__ import annotations

from kogwistar_llm_wiki.email import EmailEvidenceRecord, EmailProposalMaterializer
from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.utils import _temporary_namespace


def _record() -> EmailEvidenceRecord:
    return EmailEvidenceRecord(
        workspace_id="w",
        stream_id="stream-a",
        source_key="uid:1",
        source_revision_id="revision-1",
        content_sha256="a" * 64,
        parsed_payload={},
        mapping_payload={
            "mapping_id": "mapping-1",
            "entities": [
                {"entity_id": "message", "class_id": "EmailMessage", "properties": {"subject": ["Hello"]}},
                {"entity_id": "person", "class_id": "EmailAddress", "properties": {"address": ["a@example.test"]}},
            ],
            "relations": [
                {
                    "relation_id": "message_exchange",
                    "subject_id": "message",
                    "target_ids": ["person"],
                    "roles": {},
                }
            ],
        },
        blob_ref="sha256:" + "a" * 64,
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
