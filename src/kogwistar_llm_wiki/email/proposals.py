"""Durable, non-authoritative records for email ontology mapping proposals."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .runtime import EmailEvidenceRecord


@dataclass(frozen=True, slots=True)
class EmailMappingProposal:
    """A persisted candidate that still requires explicit acceptance."""

    workspace_id: str
    stream_id: str
    source_revision_id: str
    source_document_id: str
    mapping_id: str
    mapping_payload: Mapping[str, object]
    evidence: tuple[Mapping[str, object], ...]
    scores: Mapping[str, float]
    ontology_identity: Mapping[str, object]
    composition_sha256: str | None = None
    plugin_version: str | None = None
    model_revision: str | None = None
    created_at_ms: int = 0

    def __post_init__(self) -> None:
        for name in (
            "workspace_id",
            "stream_id",
            "source_revision_id",
            "source_document_id",
            "mapping_id",
        ):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)
        if not isinstance(self.mapping_payload, Mapping):
            raise TypeError("mapping_payload must be an object")
        if not self.evidence:
            raise ValueError("mapping proposal requires at least one evidence item")
        if not isinstance(self.ontology_identity, Mapping):
            raise TypeError("ontology_identity must be an object")
        for descriptor_id, score in self.scores.items():
            if not str(descriptor_id).strip():
                raise ValueError("proposal score descriptor IDs must not be empty")
            numeric = float(score)
            if numeric < 0.0 or numeric > 1.0:
                raise ValueError("proposal scores must be between 0 and 1")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must not be negative")

    @classmethod
    def from_record(
        cls,
        record: EmailEvidenceRecord,
        *,
        confidence: float,
        composition_sha256: str | None = None,
        plugin_version: str | None = None,
        model_revision: str | None = None,
    ) -> EmailMappingProposal:
        if not record.source_document_id:
            raise ValueError("email evidence lacks authoritative source_document_id")
        mapping_id = str(record.mapping_payload.get("mapping_id") or "").strip()
        if not mapping_id:
            raise ValueError("email mapping did not provide a deterministic mapping_id")
        ontology = record.mapping_payload.get("ontology")
        if not isinstance(ontology, Mapping):
            raise TypeError("email mapping ontology identity must be an object")
        descriptor_ids = _descriptor_ids(record.mapping_payload)
        score = float(confidence)
        if score < 0.0 or score > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        subject = str(record.parsed_payload.get("subject") or "").strip()
        evidence = {
            "kind": "source_span",
            "source_document_id": record.source_document_id,
            "source_revision_id": record.source_revision_id,
            "content_sha256": record.content_sha256,
            "locator": f"email://{record.stream_id}/{record.source_revision_id}",
            "excerpt": (f"Subject: {subject}" if subject else "Email mapping evidence")[:4000],
        }
        return cls(
            workspace_id=record.workspace_id,
            stream_id=record.stream_id,
            source_revision_id=record.source_revision_id,
            source_document_id=record.source_document_id,
            mapping_id=mapping_id,
            mapping_payload=dict(record.mapping_payload),
            evidence=(evidence,),
            scores={descriptor_id: score for descriptor_id in descriptor_ids},
            ontology_identity=dict(ontology),
            composition_sha256=composition_sha256,
            plugin_version=plugin_version,
            model_revision=model_revision,
            created_at_ms=int(time.time() * 1000),
        )

    def payload(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "stream_id": self.stream_id,
            "source_revision_id": self.source_revision_id,
            "source_document_id": self.source_document_id,
            "mapping_id": self.mapping_id,
            "mapping_payload": dict(self.mapping_payload),
            "evidence": [dict(item) for item in self.evidence],
            "scores": {str(key): float(value) for key, value in self.scores.items()},
            "ontology_identity": dict(self.ontology_identity),
            "composition_sha256": self.composition_sha256,
            "plugin_version": self.plugin_version,
            "model_revision": self.model_revision,
            "created_at_ms": self.created_at_ms,
        }


class EmailMappingProposalStore(Protocol):
    def get(
        self,
        *,
        workspace_id: str,
        source_revision_id: str,
        mapping_id: str,
    ) -> EmailMappingProposal | None: ...

    def put(self, proposal: EmailMappingProposal) -> None: ...


class InMemoryEmailMappingProposalStore:
    def __init__(self) -> None:
        self._proposals: dict[tuple[str, str, str], EmailMappingProposal] = {}

    def get(
        self,
        *,
        workspace_id: str,
        source_revision_id: str,
        mapping_id: str,
    ) -> EmailMappingProposal | None:
        return self._proposals.get((workspace_id, source_revision_id, mapping_id))

    def put(self, proposal: EmailMappingProposal) -> None:
        key = (proposal.workspace_id, proposal.source_revision_id, proposal.mapping_id)
        existing = self._proposals.get(key)
        if existing is not None and existing.payload() != proposal.payload():
            raise ValueError("mapping proposal already exists with different content")
        self._proposals[key] = proposal


class SQLiteEmailMappingProposalStore:
    """SQLite-backed proposal storage separate from immutable email evidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_mapping_proposals (
                    workspace_id TEXT NOT NULL,
                    source_revision_id TEXT NOT NULL,
                    mapping_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, source_revision_id, mapping_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def get(
        self,
        *,
        workspace_id: str,
        source_revision_id: str,
        mapping_id: str,
    ) -> EmailMappingProposal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM email_mapping_proposals "
                "WHERE workspace_id = ? AND source_revision_id = ? AND mapping_id = ?",
                (workspace_id, source_revision_id, mapping_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        return _from_payload(payload)

    def put(self, proposal: EmailMappingProposal) -> None:
        encoded = json.dumps(proposal.payload(), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM email_mapping_proposals "
                "WHERE workspace_id = ? AND source_revision_id = ? AND mapping_id = ?",
                (proposal.workspace_id, proposal.source_revision_id, proposal.mapping_id),
            ).fetchone()
            if existing is not None and str(existing["payload_json"]) != encoded:
                raise ValueError("mapping proposal already exists with different content")
            connection.execute(
                "INSERT OR IGNORE INTO email_mapping_proposals "
                "(workspace_id, source_revision_id, mapping_id, payload_json) VALUES (?, ?, ?, ?)",
                (proposal.workspace_id, proposal.source_revision_id, proposal.mapping_id, encoded),
            )


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


def _from_payload(payload: Mapping[str, object]) -> EmailMappingProposal:
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, Mapping) for item in evidence):
        raise TypeError("stored proposal evidence must be an array of objects")
    scores = payload.get("scores")
    if not isinstance(scores, Mapping):
        raise TypeError("stored proposal scores must be an object")
    return EmailMappingProposal(
        workspace_id=str(payload["workspace_id"]),
        stream_id=str(payload["stream_id"]),
        source_revision_id=str(payload["source_revision_id"]),
        source_document_id=str(payload["source_document_id"]),
        mapping_id=str(payload["mapping_id"]),
        mapping_payload=payload["mapping_payload"],
        evidence=tuple(dict(item) for item in evidence),
        scores={str(key): float(value) for key, value in scores.items()},
        ontology_identity=payload["ontology_identity"],
        composition_sha256=(
            str(payload["composition_sha256"])
            if payload.get("composition_sha256") is not None
            else None
        ),
        plugin_version=(str(payload["plugin_version"]) if payload.get("plugin_version") else None),
        model_revision=(str(payload["model_revision"]) if payload.get("model_revision") else None),
        created_at_ms=int(payload.get("created_at_ms", 0)),
    )


__all__ = [
    "EmailMappingProposal",
    "EmailMappingProposalStore",
    "InMemoryEmailMappingProposalStore",
    "SQLiteEmailMappingProposalStore",
]
