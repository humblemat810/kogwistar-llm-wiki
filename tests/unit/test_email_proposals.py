from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from kogwistar_llm_wiki.email import (
    EmailEvidenceRecord,
    EmailMappingProposal,
    InMemoryEmailMappingProposalStore,
    SQLiteEmailMappingProposalStore,
)


def _record() -> EmailEvidenceRecord:
    return EmailEvidenceRecord(
        workspace_id="workspace-a",
        stream_id="mail:stream-a",
        source_key="uid:1",
        source_revision_id="revision-1",
        content_sha256="a" * 64,
        parsed_payload={"subject": "Quarterly update", "parser_profile": "rfc822-mime-v1"},
        mapping_payload={
            "mapping_id": "mapping-1",
            "ontology": {
                "ontology_id": "email",
                "version": "1.0.0",
                "content_sha256": "b" * 64,
                "schema_version": 1,
            },
            "entities": [{"entity_id": "message", "class_id": "EmailMessage"}],
            "relations": [{"relation_id": "message_exchange"}],
        },
        blob_ref="sha256:" + "a" * 64,
        source_document_id="email-source:1",
    )


def _proposal() -> EmailMappingProposal:
    return EmailMappingProposal.from_record(
        _record(),
        confidence=0.8,
        composition_sha256="c" * 64,
        plugin_version="rfc822-mime-v1",
        model_revision="model-1",
    )


@pytest.mark.parametrize("store_factory", [InMemoryEmailMappingProposalStore, SQLiteEmailMappingProposalStore])
def test_mapping_proposals_round_trip_and_reject_conflicting_replay(
    store_factory,
    tmp_path: Path,
) -> None:
    store = (
        store_factory()
        if store_factory is InMemoryEmailMappingProposalStore
        else store_factory(tmp_path / "proposals.sqlite")
    )
    proposal = _proposal()
    store.put(proposal)
    loaded = store.get(
        workspace_id=proposal.workspace_id,
        source_revision_id=proposal.source_revision_id,
        mapping_id=proposal.mapping_id,
    )

    assert loaded is not None
    assert loaded.payload() == proposal.payload()
    store.put(proposal)

    changed = EmailMappingProposal.from_record(_record(), confidence=0.2)
    with pytest.raises(ValueError, match="already exists with different content"):
        store.put(changed)


def test_mapping_proposal_requires_pinned_ontology_identity() -> None:
    record = _record()
    record = replace(
        record,
        mapping_payload={"mapping_id": "mapping-1", "entities": [], "relations": []},
    )
    with pytest.raises((ValueError, TypeError), match="ontology"):
        EmailMappingProposal.from_record(record, confidence=0.8)
