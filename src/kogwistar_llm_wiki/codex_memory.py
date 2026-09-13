"""Project-scoped, evidence-backed memory for Codex MCP clients."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from typing import Literal

from kogwistar.engine_core.models import Edge, Grounding, Node, Span
from kogwistar.id_provider import stable_id
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace

MemoryKind = Literal["decision", "constraint", "convention", "finding", "task_outcome"]
MemoryConfidence = Literal["verified", "inferred"]
EvidenceKind = Literal["source_span", "repository"]

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_REVISION = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SECRET_LIKE = re.compile(
    r"(?ix)"
    r"(?:api[_-]?key|password|private\s+key|secret\s*[:=]\s*\S+|"
    r"token\s*[:=]\s*\S+|bearer\s+[a-z0-9._~-]{12,}|"
    r"-----begin\s+[^\n]*private\s+key-----)"
)
_WORD = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")


class CodexMemoryError(ValueError):
    """A rejected memory request that must not reach graph persistence."""


class MemoryDisabledError(CodexMemoryError):
    """Autonomous memory capture is not enabled for this process."""


class MemoryEvidence(BaseModel):
    """A bounded reference to evidence already known to the caller."""

    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    evidence_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_document_id: str | None = Field(default=None, max_length=200)
    span: dict[str, object] | None = None
    repository_path: str | None = Field(default=None, max_length=500)
    revision: str | None = Field(default=None, max_length=64)
    content_sha256: str | None = None
    start_line: int | None = Field(default=None, ge=1, le=10_000_000)
    end_line: int | None = Field(default=None, ge=1, le=10_000_000)
    excerpt: str = Field(default="", max_length=4000)
    locator: str | None = Field(default=None, max_length=1000)

    @field_validator("repository_path", "locator")
    @classmethod
    def _reject_paths_and_secrets(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if "\\" in text or text.lower().startswith("file:"):
            raise CodexMemoryError("memory evidence must not contain host filesystem paths")
        if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
            raise CodexMemoryError("memory evidence must not contain drive-qualified paths")
        if _SECRET_LIKE.search(text):
            raise CodexMemoryError("memory evidence contains secret-like content")
        return text

    @field_validator("revision")
    @classmethod
    def _valid_revision(cls, value: str | None) -> str | None:
        if value is not None and not _REVISION.fullmatch(value.strip()):
            raise CodexMemoryError("repository evidence revision must be a commit hash")
        return value.strip() if value is not None else None

    @field_validator("content_sha256")
    @classmethod
    def _valid_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value.strip()):
            raise CodexMemoryError("repository evidence content_sha256 must be a SHA-256 hash")
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def _validate_shape(self) -> MemoryEvidence:
        if self.kind == "repository":
            if not self.repository_path or self.repository_path.startswith("/"):
                raise CodexMemoryError("repository evidence requires a relative repository_path")
            if any(part == ".." for part in self.repository_path.split("/")):
                raise CodexMemoryError("repository evidence path may not escape the repository")
            if not self.revision or not self.content_sha256:
                raise CodexMemoryError("repository evidence requires revision and content_sha256")
            if self.start_line is None or self.end_line is None or self.start_line > self.end_line:
                raise CodexMemoryError("repository evidence requires an ordered line range")
        elif not self.source_document_id:
            raise CodexMemoryError("source_span evidence requires source_document_id")
        if self.span is not None:
            Span.model_validate(self.span)
        if _SECRET_LIKE.search(self.excerpt):
            raise CodexMemoryError("memory evidence excerpt contains secret-like content")
        return self

    def stable_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"evidence_id"})
        return str(stable_id("kogwistar_llm_wiki.codex_memory.evidence", json.dumps(payload, sort_keys=True)))


class CodexMemoryRecord(BaseModel):
    """An immutable project memory candidate with explicit grounding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    kind: MemoryKind
    statement: str = Field(min_length=1, max_length=10_000)
    rationale: str = Field(default="", max_length=10_000)
    confidence: MemoryConfidence
    evidence: tuple[MemoryEvidence, ...] = Field(min_length=1, max_length=16)
    related_memory_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    investigation_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    supersedes_memory_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    conflicts_with_memory_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    source_watermark: str | int | None = None
    capture_policy_version: str = Field(default="1", min_length=1, max_length=40)
    lifecycle_status: Literal["candidate", "reviewed", "superseded", "retired"] = "candidate"
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000), ge=0)

    @field_validator("workspace_id", "session_id", "statement", "rationale")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        text = value.strip()
        if _SECRET_LIKE.search(text):
            raise CodexMemoryError("memory content contains secret-like content")
        return text

    @field_validator(
        "related_memory_ids",
        "investigation_ids",
        "supersedes_memory_ids",
        "conflicts_with_memory_ids",
    )
    @classmethod
    def _clean_link_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(value.strip() for value in values)
        if any(not value for value in cleaned):
            raise CodexMemoryError("memory relationship IDs must be non-empty")
        return cleaned

    @model_validator(mode="after")
    def _require_grounding(self) -> CodexMemoryRecord:
        if self.confidence in {"verified", "inferred"} and not self.evidence:
            raise CodexMemoryError("memory records require at least one evidence reference")
        linked_ids = (
            *self.related_memory_ids,
            *self.supersedes_memory_ids,
            *self.conflicts_with_memory_ids,
        )
        if self.memory_id() in linked_ids:
            raise CodexMemoryError("a memory record cannot link to itself")
        return self

    def memory_id(self) -> str:
        evidence_ids = [item.evidence_id or item.stable_id() for item in self.evidence]
        return str(
            stable_id(
                "kogwistar_llm_wiki.codex_memory.record",
                self.workspace_id,
                self.kind,
                self.statement,
                json.dumps(sorted(evidence_ids)),
            )
        )


class CodexMemoryService:
    """Persist and retrieve project memory using existing graph primitives."""

    artifact_kind = "codex_memory"
    evidence_artifact_kind = "codex_memory_evidence"

    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        enabled: bool | None = None,
        max_records_per_capture: int | None = None,
        max_recall_records: int | None = None,
    ) -> None:
        self.engines = engines
        self.enabled = _env_truthy("LLM_WIKI_CODEX_MEMORY_ENABLED") if enabled is None else enabled
        self.max_records_per_capture = _bounded_int(
            max_records_per_capture if max_records_per_capture is not None else os.getenv("LLM_WIKI_CODEX_MEMORY_MAX_RECORDS_PER_CAPTURE", "8"),
            default=8,
            upper=32,
        )
        self.max_recall_records = _bounded_int(
            max_recall_records if max_recall_records is not None else os.getenv("LLM_WIKI_CODEX_MEMORY_MAX_RECALL_RECORDS", "12"),
            default=12,
            upper=100,
        )

    def capture(self, payload: Mapping[str, object] | Sequence[Mapping[str, object]]) -> dict[str, object]:
        if not self.enabled:
            raise MemoryDisabledError(
                "autonomous Codex memory is disabled; set LLM_WIKI_CODEX_MEMORY_ENABLED=true"
            )
        raw_records = [payload] if isinstance(payload, Mapping) else list(payload)
        if not raw_records or len(raw_records) > self.max_records_per_capture:
            raise CodexMemoryError(
                f"memory capture accepts 1..{self.max_records_per_capture} records"
            )
        records: list[dict[str, object]] = []
        validated: list[CodexMemoryRecord] = []
        for raw in raw_records:
            record = CodexMemoryRecord.model_validate(raw)
            validated.append(record)
        workspace_ids = {record.workspace_id for record in validated}
        if len(workspace_ids) != 1:
            raise CodexMemoryError("a memory capture batch must contain one workspace only")
        for record in validated:
            records.append(self._persist(record))
        return {"status": "captured", "workspace_id": records[0]["workspace_id"], "records": records}

    def recall(
        self,
        *,
        workspace_id: str,
        query_text: str = "",
        include_inferred: bool = True,
        limit: int | None = None,
    ) -> dict[str, object]:
        if not workspace_id.strip():
            raise CodexMemoryError("memory recall requires workspace_id")
        records = self._read_records(workspace_id)
        if not include_inferred:
            records = [item for item in records if item.confidence != "inferred"]
        query_words = {word.lower() for word in _WORD.findall(query_text)}
        ranked: list[tuple[float, CodexMemoryRecord]] = []
        for record in records:
            words = {word.lower() for word in _WORD.findall(f"{record.statement} {record.rationale}")}
            overlap = len(query_words & words) / max(1, len(query_words)) if query_words else 0.0
            confidence_bonus = 0.1 if record.confidence == "verified" else 0.0
            ranked.append((overlap + confidence_bonus, record))
        ranked.sort(key=lambda item: (-item[0], -item[1].created_at_ms, item[1].memory_id()))
        maximum = self.max_recall_records if limit is None else _bounded_int(limit, default=self.max_recall_records, upper=self.max_recall_records)
        selected = ranked[:maximum]
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "enabled": self.enabled,
            "query_text": query_text,
            "verified": [self._record_payload(record, score=score) for score, record in selected if record.confidence == "verified"],
            "inferred": [self._record_payload(record, score=score) for score, record in selected if record.confidence == "inferred"],
            "count": len(selected),
        }

    def review(
        self,
        *,
        workspace_id: str,
        kind: str | None = None,
        confidence: str | None = None,
        lifecycle_status: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        if not workspace_id.strip():
            raise CodexMemoryError("memory review requires workspace_id")
        if kind is not None and kind not in {"decision", "constraint", "convention", "finding", "task_outcome"}:
            raise CodexMemoryError("memory review kind is invalid")
        if confidence is not None and confidence not in {"verified", "inferred"}:
            raise CodexMemoryError("memory review confidence is invalid")
        if lifecycle_status is not None and lifecycle_status not in {"candidate", "reviewed", "superseded", "retired"}:
            raise CodexMemoryError("memory review lifecycle_status is invalid")
        records = self._read_records(workspace_id)
        filters = {"kind": kind, "confidence": confidence, "lifecycle_status": lifecycle_status}
        records = [
            record
            for record in records
            if all(value is None or getattr(record, key) == value for key, value in filters.items())
        ]
        records.sort(key=lambda item: (-item.created_at_ms, item.memory_id()))
        selected = records[: _bounded_int(limit, default=50, upper=100)]
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "enabled": self.enabled,
            "records": [self._record_payload(record) for record in selected],
            "count": len(selected),
        }

    def _persist(self, record: CodexMemoryRecord) -> dict[str, object]:
        memory_id = record.memory_id()
        evidence = [item.model_copy(update={"evidence_id": item.evidence_id or item.stable_id()}) for item in record.evidence]
        record = record.model_copy(update={"evidence": tuple(evidence)})
        namespace = WorkspaceNamespaces(record.workspace_id).conv_fg
        with _temporary_namespace(self.engines.conversation, namespace):
            existing = self.engines.conversation.read.get_nodes(ids=[memory_id], limit=1)
            if not existing:
                linked_ids = (
                    *record.related_memory_ids,
                    *record.investigation_ids,
                    *record.supersedes_memory_ids,
                    *record.conflicts_with_memory_ids,
                )
                missing_links = [
                    target_id
                    for target_id in linked_ids
                    if not self.engines.conversation.read.get_nodes(ids=[target_id], limit=1)
                    and not self.engines.conversation.read.get_edges(ids=[target_id], limit=1)
                ]
                if missing_links:
                    raise CodexMemoryError(
                        "memory link targets must already exist; missing: "
                        + ", ".join(sorted(set(missing_links)))
                    )
                for item in evidence:
                    evidence_id = item.evidence_id or item.stable_id()
                    if not self.engines.conversation.read.get_nodes(ids=[evidence_id], limit=1):
                        self.engines.conversation.write.add_node(_evidence_node(record, item, evidence_id))
                self.engines.conversation.write.add_node(_memory_node(record, memory_id))
                self.engines.conversation.write.add_edge(_support_edge(record, memory_id, evidence))
                for target_id in record.related_memory_ids:
                    self.engines.conversation.write.add_edge(
                        _memory_relation_edge(record, memory_id, target_id, "related_to")
                    )
                for target_id in record.investigation_ids:
                    self.engines.conversation.write.add_edge(
                        _memory_relation_edge(record, memory_id, target_id, "informed_by_investigation")
                    )
                for target_id in record.supersedes_memory_ids:
                    self.engines.conversation.write.add_edge(
                        _memory_relation_edge(record, memory_id, target_id, "supersedes")
                    )
                for target_id in record.conflicts_with_memory_ids:
                    self.engines.conversation.write.add_edge(
                        _memory_relation_edge(record, memory_id, target_id, "conflicts_with")
                    )
        return self._record_payload(record, persisted=not bool(existing))

    def _read_records(self, workspace_id: str) -> list[CodexMemoryRecord]:
        namespace = WorkspaceNamespaces(workspace_id).conv_fg
        with _temporary_namespace(self.engines.conversation, namespace):
            # Filter after reconstruction because PostgreSQL, Chroma, and the
            # in-memory backend do not share identical nested-where behavior.
            nodes = self.engines.conversation.read.get_nodes(limit=1000)
        records: list[CodexMemoryRecord] = []
        for node in nodes:
            metadata = dict(getattr(node, "metadata", {}) or {})
            if metadata.get("workspace_id") != workspace_id or metadata.get("artifact_kind") != self.artifact_kind:
                continue
            raw = metadata.get("memory_payload_json")
            if isinstance(raw, str):
                try:
                    records.append(CodexMemoryRecord.model_validate(json.loads(raw)))
                except (json.JSONDecodeError, ValueError):
                    continue
        return records

    def _record_payload(self, record: CodexMemoryRecord, **extra: object) -> dict[str, object]:
        payload = record.model_dump(mode="json")
        payload["memory_id"] = record.memory_id()
        payload["evidence"] = [
            {**item.model_dump(mode="json"), "evidence_id": item.evidence_id or item.stable_id()}
            for item in record.evidence
        ]
        payload.update(extra)
        return payload


def _memory_node(record: CodexMemoryRecord, memory_id: str) -> Node:
    payload = record.model_dump(mode="json")
    span = Span.from_dummy_for_conversation(f"memory:{memory_id}")
    return Node(
        id=memory_id,
        label=f"Codex memory: {record.kind}",
        type="entity",
        summary=record.statement,
        doc_id=f"_conv:{memory_id}",
        mentions=[Grounding(spans=[span])],
        metadata={
            "workspace_id": record.workspace_id,
            "conversation_lane": "foreground",
            "artifact_kind": CodexMemoryService.artifact_kind,
            "memory_payload_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            "memory_id": memory_id,
            "memory_kind": record.kind,
            "memory_confidence": record.confidence,
            "memory_lifecycle_status": record.lifecycle_status,
        },
    )


def _evidence_node(record: CodexMemoryRecord, evidence: MemoryEvidence, evidence_id: str) -> Node:
    excerpt = evidence.excerpt or evidence.locator or evidence.repository_path or evidence.source_document_id or evidence_id
    span = Span.from_dummy_for_conversation(f"evidence:{evidence_id}")
    return Node(
        id=evidence_id,
        label=f"Codex evidence: {evidence.kind}",
        type="entity",
        summary=excerpt[:10_000],
        doc_id=f"_conv:{evidence_id}",
        mentions=[Grounding(spans=[span])],
        metadata={
            "workspace_id": record.workspace_id,
            "conversation_lane": "foreground",
            "artifact_kind": CodexMemoryService.evidence_artifact_kind,
            "evidence_payload_json": json.dumps(evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            "evidence_id": evidence_id,
        },
    )


def _support_edge(record: CodexMemoryRecord, memory_id: str, evidence: Sequence[MemoryEvidence]) -> Edge:
    evidence_ids = [item.evidence_id or item.stable_id() for item in evidence]
    edge_id = str(stable_id("kogwistar_llm_wiki.codex_memory.support", memory_id, json.dumps(evidence_ids)))
    return Edge(
        id=edge_id,
        label="Codex memory support",
        type="relationship",
        summary=f"{memory_id} is supported by {len(evidence_ids)} evidence references",
        doc_id=f"_conv:{memory_id}",
        source_ids=[memory_id],
        target_ids=evidence_ids,
        relation="supported_by",
        source_edge_ids=[],
        target_edge_ids=[],
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation(f"support:{edge_id}")])],
        metadata={
            "workspace_id": record.workspace_id,
            "conversation_lane": "foreground",
            "artifact_kind": CodexMemoryService.artifact_kind,
            "edge_kind": "hyperedge",
            "memory_id": memory_id,
            "evidence_ids": evidence_ids,
        },
    )


def _memory_relation_edge(
    record: CodexMemoryRecord,
    memory_id: str,
    target_id: str,
    relation: str,
) -> Edge:
    edge_id = str(stable_id("kogwistar_llm_wiki.codex_memory.relation", memory_id, relation, target_id))
    return Edge(
        id=edge_id,
        label=f"Codex memory {relation}",
        type="relationship",
        summary=f"{memory_id} {relation} {target_id}",
        doc_id=f"_conv:{memory_id}",
        source_ids=[memory_id],
        target_ids=[target_id],
        relation=relation,
        source_edge_ids=[],
        target_edge_ids=[],
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation(f"relation:{edge_id}")])],
        metadata={
            "workspace_id": record.workspace_id,
            "conversation_lane": "foreground",
            "artifact_kind": CodexMemoryService.artifact_kind,
            "edge_kind": "memory_relation",
            "memory_id": memory_id,
            "target_id": target_id,
        },
    )


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: object, *, default: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, upper))


__all__ = [
    "CodexMemoryError",
    "CodexMemoryRecord",
    "CodexMemoryService",
    "MemoryDisabledError",
    "MemoryEvidence",
]
