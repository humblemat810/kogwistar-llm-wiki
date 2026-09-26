"""Explicit, evidence-backed promotion of accepted email mappings to memory."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..memory import MemoryService
from .review import EmailReviewStateStore
from .runtime import EmailEvidenceRecord, EmailEvidenceStore


@dataclass(frozen=True, slots=True)
class EmailMemoryPromotion:
    """A validated memory payload that is still awaiting explicit confirmation."""

    mapping_id: str
    source_document_id: str
    payload: Mapping[str, object]
    descriptor_ids: tuple[str, ...] = ()
    ontology_identity: Mapping[str, object] | None = None
    source_document_ids: tuple[str, ...] = ()
    source_stream_ids: tuple[str, ...] = ()
    ontology_identities: tuple[Mapping[str, object], ...] = ()


class EmailMemoryPromotionService:
    """Build and explicitly persist memory from an accepted email mapping."""

    def __init__(
        self,
        *,
        evidence_store: EmailEvidenceStore,
        review_store: EmailReviewStateStore,
        memory_service: MemoryService,
        authorize_stream: Callable[[str, str], bool],
    ) -> None:
        self.evidence_store = evidence_store
        self.review_store = review_store
        self.memory_service = memory_service
        self.authorize_stream = authorize_stream

    def build(
        self,
        *,
        workspace_id: str,
        stream_id: str,
        source_revision_id: str,
        statement: str,
        kind: str = "finding",
        confidence: str = "inferred",
        session_id: str | None = None,
        rationale: str = "",
    ) -> EmailMemoryPromotion:
        return self.build_from_revisions(
            workspace_id=workspace_id,
            source_revision_ids=(source_revision_id,),
            expected_stream_ids=(stream_id,),
            statement=statement,
            kind=kind,
            confidence=confidence,
            session_id=session_id,
            rationale=rationale,
        )

    def build_from_revisions(
        self,
        *,
        workspace_id: str,
        source_revision_ids: Sequence[str],
        expected_stream_ids: Sequence[str] | None = None,
        statement: str,
        kind: str = "finding",
        confidence: str = "inferred",
        session_id: str | None = None,
        rationale: str = "",
    ) -> EmailMemoryPromotion:
        revisions = tuple(dict.fromkeys(str(value).strip() for value in source_revision_ids))
        if not revisions or any(not value for value in revisions):
            raise ValueError("source_revision_ids must contain at least one revision")
        expected = tuple(dict.fromkeys(str(value).strip() for value in (expected_stream_ids or ())))
        if expected and len(expected) != len(revisions):
            raise ValueError("expected_stream_ids must align with source_revision_ids")
        records: list[EmailEvidenceRecord] = []
        for index, revision_id in enumerate(revisions):
            record = self.evidence_store.get(
                workspace_id=workspace_id,
                source_revision_id=revision_id,
            )
            if record is None:
                raise KeyError(f"email evidence was not found: {revision_id}")
            if record.workspace_id != workspace_id:
                raise PermissionError("email evidence crosses workspace boundary")
            if expected and record.stream_id != expected[index]:
                raise PermissionError("email evidence stream does not match requested scope")
            if not self.authorize_stream(workspace_id, record.stream_id):
                raise PermissionError("email stream is not authorized for workspace")
            if not record.source_document_id:
                raise ValueError("email evidence lacks authoritative source_document_id")
            records.append(record)

        mapping_ids: list[str] = []
        descriptor_id_values: set[str] = set()
        ontology_identities: list[Mapping[str, object]] = []
        evidence_payloads: list[dict[str, object]] = []
        for record in records:
            mapping_id = str(record.mapping_payload.get("mapping_id") or "")
            if not mapping_id:
                raise ValueError("email mapping did not provide a deterministic mapping_id")
            ontology_identity = record.mapping_payload.get("ontology")
            if ontology_identity is None:
                raise ValueError("email mapping lacks a pinned ontology identity")
            if not isinstance(ontology_identity, Mapping):
                raise TypeError("email mapping ontology identity must be an object")
            required_identity = {"ontology_id", "version", "content_sha256", "schema_version"}
            if not required_identity.issubset(ontology_identity):
                raise ValueError("email mapping ontology identity is incomplete")
            review = self.review_store.get(
                workspace_id=workspace_id,
                source_revision_id=record.source_revision_id,
                mapping_id=mapping_id,
            )
            if review is None or review.status != "accepted":
                raise ValueError("email mapping must be accepted before memory promotion")
            if review.source_document_id != record.source_document_id:
                raise ValueError("email review state is not bound to immutable evidence")
            mapping_ids.append(mapping_id)
            descriptor_id_values.update(_descriptor_ids(record.mapping_payload))
            ontology_identities.append(dict(ontology_identity))
            evidence_payloads.append(self._evidence_payload(record))

        source_stream_ids = tuple(sorted({record.stream_id for record in records}))
        source_document_ids = tuple(str(record.source_document_id) for record in records)
        combined_mapping_id = (
            mapping_ids[0]
            if len(mapping_ids) == 1
            else "email-mappings:"
            + hashlib.sha256("|".join(sorted(mapping_ids)).encode("utf-8")).hexdigest()
        )
        cleaned_statement = statement.strip()
        if not cleaned_statement:
            raise ValueError("memory statement must not be empty")
        metadata: dict[str, object] = {
            "ontology": dict(ontology_identities[0]),
            "descriptor_ids": sorted(descriptor_id_values),
            "source_stream_ids": list(source_stream_ids),
            "acl": {
                "workspace_id": workspace_id,
                "stream_ids": list(source_stream_ids),
            },
        }
        if len(ontology_identities) > 1:
            metadata["ontology_identities"] = [dict(item) for item in ontology_identities]
        payload: dict[str, object] = {
            "workspace_id": workspace_id,
            "session_id": session_id or f"email:{combined_mapping_id}",
            "kind": kind,
            "statement": cleaned_statement,
            "confidence": confidence,
            "rationale": rationale.strip()
            or f"Accepted ontology mapping {combined_mapping_id} from email evidence",
            "metadata": metadata,
            "evidence": evidence_payloads,
        }
        return EmailMemoryPromotion(
            mapping_id=combined_mapping_id,
            source_document_id=source_document_ids[0],
            payload=payload,
            descriptor_ids=tuple(sorted(descriptor_id_values)),
            ontology_identity=dict(ontology_identities[0]),
            source_document_ids=source_document_ids,
            source_stream_ids=source_stream_ids,
            ontology_identities=tuple(ontology_identities),
        )

    def promote(
        self,
        promotion: EmailMemoryPromotion,
        *,
        confirmed: bool,
    ) -> dict[str, object] | None:
        if not confirmed:
            return None
        return self.memory_service.capture(promotion.payload)

    @staticmethod
    def _evidence_payload(record: EmailEvidenceRecord) -> dict[str, object]:
        parsed = record.parsed_payload
        subject = str(parsed.get("subject") or "").strip()
        excerpt = f"Subject: {subject}" if subject else "Email mapping evidence"
        return {
            "kind": "source_span",
            "source_document_id": record.source_document_id,
            "content_sha256": record.content_sha256,
            "locator": f"email://{record.stream_id}/{record.source_revision_id}",
            "excerpt": excerpt[:4000],
        }


def _descriptor_ids(mapping: Mapping[str, object]) -> tuple[str, ...]:
    values: set[str] = set()
    for key, descriptor_key in (("entities", "class_id"), ("relations", "relation_id")):
        entries = mapping.get(key)
        if not isinstance(entries, list):
            raise TypeError(f"email mapping {key} must be an array")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise TypeError(f"email mapping {key} must contain objects")
            descriptor_id = str(entry.get(descriptor_key) or "").strip()
            if not descriptor_id:
                raise ValueError(f"email mapping {key} contains an empty descriptor ID")
            values.add(descriptor_id)
    return tuple(sorted(values))


__all__ = ["EmailMemoryPromotion", "EmailMemoryPromotionService"]
