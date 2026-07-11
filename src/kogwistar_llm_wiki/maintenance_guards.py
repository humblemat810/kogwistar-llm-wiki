from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from kogwistar.id_provider import stable_id


GuardStatus = Literal["ready", "blocked", "stale"]


@dataclass(frozen=True, slots=True)
class SourceRevision:
    source_document_id: str
    revision_id: str
    source_digest: str


@dataclass(frozen=True, slots=True)
class MaintenanceGuardDecision:
    status: GuardStatus
    reason: str
    source_document_id: str
    source_revision_id: str
    source_digest: str
    required_stage: str


def source_digest(raw_text: str) -> str:
    return hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()


def build_source_revision(
    *,
    workspace_id: str,
    source_document_id: str,
    raw_text: str,
    attempt_id: str | None = None,
) -> SourceRevision:
    digest = source_digest(raw_text)
    revision_id = str(
        stable_id(
            "kogwistar_llm_wiki.source_revision",
            workspace_id,
            source_document_id,
            digest,
            attempt_id or "content",
        )
    )
    return SourceRevision(
        source_document_id=source_document_id,
        revision_id=revision_id,
        source_digest=digest,
    )


def required_stage_for_maintenance(maintenance_kind: str) -> str:
    if maintenance_kind == "document_seed_graph":
        return "source_map_seeded"
    return "parsed_graph_persisted"


def readiness_id(*, source_document_id: str, revision_id: str, stage: str) -> str:
    return str(
        stable_id(
            "kogwistar_llm_wiki.source_readiness",
            source_document_id,
            revision_id,
            stage,
        )
    )


def evaluate_maintenance_guard(
    *,
    source_revision: SourceRevision | None,
    requested_revision_id: str,
    requested_digest: str,
    required_stage: str,
    ready_revision_ids: set[str],
) -> MaintenanceGuardDecision:
    source_document_id = source_revision.source_document_id if source_revision else ""
    if source_revision is None:
        return MaintenanceGuardDecision(
            status="blocked",
            reason="source_revision_missing",
            source_document_id=source_document_id,
            source_revision_id=requested_revision_id,
            source_digest=requested_digest,
            required_stage=required_stage,
        )
    if source_revision.revision_id != requested_revision_id or source_revision.source_digest != requested_digest:
        return MaintenanceGuardDecision(
            status="stale",
            reason="source_revision_mismatch",
            source_document_id=source_revision.source_document_id,
            source_revision_id=requested_revision_id,
            source_digest=requested_digest,
            required_stage=required_stage,
        )
    if requested_revision_id not in ready_revision_ids:
        return MaintenanceGuardDecision(
            status="blocked",
            reason=f"required_stage_missing:{required_stage}",
            source_document_id=source_revision.source_document_id,
            source_revision_id=requested_revision_id,
            source_digest=requested_digest,
            required_stage=required_stage,
        )
    return MaintenanceGuardDecision(
        status="ready",
        reason="source_revision_and_readiness_verified",
        source_document_id=source_revision.source_document_id,
        source_revision_id=requested_revision_id,
        source_digest=requested_digest,
        required_stage=required_stage,
    )
