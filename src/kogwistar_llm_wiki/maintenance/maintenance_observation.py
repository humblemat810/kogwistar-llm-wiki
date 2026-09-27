"""Bounded, read-only maintenance observation contracts."""

from __future__ import annotations

import json
import hashlib
import time
from collections.abc import Mapping, Sequence
from typing import Literal

from kogwistar.id_provider import stable_id
from pydantic import BaseModel, ConfigDict, Field


SubjectKind = Literal["node", "edge", "hyperedge"]
QualityVerdict = Literal[
    "adequate",
    "too_coarse",
    "too_fine",
    "coverage_gap",
    "overlap",
    "duplicate",
    "misgrouped",
    "weak_label",
    "ungrounded",
    "relation_unsupported",
    "relation_misplaced",
    "contradictory",
    "quality_unknown",
    "review_required",
]
RecommendedAction = Literal[
    "none",
    "review_parent",
    "expand_children",
    "retry_same_strategy",
    "switch_to_excerpt",
    "switch_to_boundary",
    "reparse_region",
    "propose_relation_patch",
    "validate_crosslinks",
    "request_human_review",
]


class ObservationSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SubjectKind
    subject_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    source_document_id: str | None = None
    revision_id: str | None = None
    revision_document_id: str | None = None
    parse_member_id: str | None = None
    acl_scope: str | None = None
    embedding_profile_fingerprint: str | None = None


class ObservationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    verdict: QualityVerdict
    severity: Literal["info", "warning", "error"]
    subject_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    message: str = Field(min_length=1, max_length=512)


class ObservationRuntimeLimits(BaseModel):
    """Bounded per-job observation knobs; all defaults are conservative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_probability: float = Field(default=1.0, ge=0.0, le=1.0)
    ancestor_hops: int = Field(default=1, ge=0, le=1)
    neighborhood_count: int = Field(default=64, ge=1, le=512)
    token_budget: int = Field(default=4_000, ge=256, le=100_000)
    watermark_expiry_seconds: int = Field(default=86_400, ge=60, le=604_800)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ObservationRuntimeLimits:
        return cls.model_validate(
            {
                "observation_probability": payload.get("observation_probability", 1.0),
                "ancestor_hops": payload.get("observation_ancestor_hops", 1),
                "neighborhood_count": payload.get("observation_neighborhood_count", 64),
                "token_budget": payload.get("observation_token_budget", 4_000),
                "watermark_expiry_seconds": payload.get(
                    "observation_watermark_expiry_seconds", 86_400
                ),
            }
        )

    def is_due(self, key: str) -> bool:
        if self.observation_probability >= 1.0:
            return True
        if self.observation_probability <= 0.0:
            return False
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        sample = int.from_bytes(digest[:8], "big") / float(2**64)
        return sample < self.observation_probability


class MaintenanceObservationFrame(BaseModel):
    """A bounded frame; it contains metadata and IDs, never raw source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_id: str = Field(min_length=1)
    subject: ObservationSubject
    source_context: tuple[dict[str, object], ...] = ()
    relation_context: tuple[dict[str, object], ...] = ()
    neighborhood_context: tuple[dict[str, object], ...] = ()
    parent_context: tuple[dict[str, object], ...] = ()
    omitted_counts: dict[str, int] = Field(default_factory=dict)
    token_budget: int = Field(ge=1)
    active_view_id: str | None = None
    active_view_version: int | None = Field(default=None, ge=1)


class ParseAndGraphQualityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment_id: str = Field(min_length=1)
    watermark_key: str = Field(min_length=1)
    frame_id: str = Field(min_length=1)
    subject: ObservationSubject
    verdict: QualityVerdict
    recommended_action: RecommendedAction
    findings: tuple[ObservationFinding, ...] = ()
    active_view_id: str | None = None
    active_view_version: int | None = Field(default=None, ge=1)
    evaluator_version: str = Field(default="maintenance-observation-v1", min_length=1)
    critic_status: Literal["not_used", "succeeded", "failed"] = "not_used"
    continuation_allowed: bool = False
    watermark_expiry_seconds: int = Field(default=86_400, ge=60)


def _bounded_records(
    records: Sequence[Mapping[str, object]],
    *,
    token_budget: int,
) -> tuple[tuple[dict[str, object], ...], int]:
    """Keep deterministic records within a conservative serialized budget."""

    output: list[dict[str, object]] = []
    used = 0
    omitted = 0
    for record in records:
        normalized = dict(record)
        size = max(1, len(json.dumps(normalized, sort_keys=True, default=str)) // 4)
        if used + size > token_budget:
            omitted += 1
            continue
        output.append(normalized)
        used += size
    return tuple(output), omitted


def _scoped_records(
    records: Sequence[Mapping[str, object]],
    *,
    subject: ObservationSubject,
) -> list[Mapping[str, object]]:
    """Drop explicitly out-of-scope or unauthorized evidence before bounding."""

    scoped: list[Mapping[str, object]] = []
    for record in records:
        workspace_id = str(record.get("workspace_id") or "").strip()
        namespace = str(record.get("namespace") or "").strip()
        if workspace_id and workspace_id != subject.workspace_id:
            continue
        if namespace and namespace != subject.namespace:
            continue
        if record.get("acl_authorized") is False or record.get("authorized") is False:
            continue
        if record.get("profile_compatible") is False:
            continue
        record_revision = str(
            record.get("source_revision_id") or record.get("revision_id") or ""
        ).strip()
        if subject.revision_id and record_revision and record_revision != subject.revision_id:
            continue
        record_revision_document = str(record.get("revision_document_id") or "").strip()
        if (
            subject.revision_document_id
            and record_revision_document
            and record_revision_document != subject.revision_document_id
        ):
            continue
        record_profile = str(
            record.get("embedding_profile_fingerprint") or record.get("profile_fingerprint") or ""
        ).strip()
        if (
            subject.embedding_profile_fingerprint
            and record_profile
            and record_profile != subject.embedding_profile_fingerprint
        ):
            continue
        scoped.append(record)
    return scoped


def build_observation_frame(
    subject: ObservationSubject,
    *,
    source_context: Sequence[Mapping[str, object]] = (),
    relation_context: Sequence[Mapping[str, object]] = (),
    neighborhood_context: Sequence[Mapping[str, object]] = (),
    parent_context: Sequence[Mapping[str, object]] = (),
    token_budget: int = 4_000,
    active_view_id: str | None = None,
    active_view_version: int | None = None,
) -> MaintenanceObservationFrame:
    """Build an auditable bounded frame before any semantic evaluation."""

    budget = max(256, int(token_budget))
    source, omitted_source = _bounded_records(
        _scoped_records(source_context, subject=subject),
        token_budget=budget * 35 // 100,
    )
    relations, omitted_relations = _bounded_records(
        _scoped_records(relation_context, subject=subject),
        token_budget=budget * 20 // 100,
    )
    neighborhood, omitted_neighborhood = _bounded_records(
        _scoped_records(neighborhood_context, subject=subject),
        token_budget=budget * 20 // 100,
    )
    parents, omitted_parents = _bounded_records(
        _scoped_records(parent_context[:1], subject=subject),
        token_budget=budget * 15 // 100,
    )
    frame_id = str(
        stable_id(
            "kogwistar_llm_wiki.maintenance_observation",
            subject.workspace_id,
            subject.namespace,
            subject.kind,
            subject.subject_id,
            subject.revision_id or "",
            subject.parse_member_id or "",
            active_view_id or "",
            active_view_version or 0,
        )
    )
    return MaintenanceObservationFrame(
        frame_id=frame_id,
        subject=subject,
        source_context=source,
        relation_context=relations,
        neighborhood_context=neighborhood,
        omitted_counts={
            "source": omitted_source,
            "relations": omitted_relations,
            "neighborhood": omitted_neighborhood,
            "parent": omitted_parents,
        },
        token_budget=budget,
        active_view_id=active_view_id,
        active_view_version=active_view_version,
        parent_context=parents,
    )


def assess_observation_frame(
    frame: MaintenanceObservationFrame,
    *,
    critic_failed: bool = False,
    watermark_expiry_seconds: int = 86_400,
) -> ParseAndGraphQualityAssessment:
    """Apply conservative deterministic checks; semantic review is optional."""

    findings: list[ObservationFinding] = []
    quality_action: RecommendedAction | None = None
    quality_verdict: QualityVerdict | None = None
    source_statuses = {
        str(item.get("quality_status") or item.get("parse_status") or "").strip().lower()
        for item in frame.source_context
    }
    source_quality_actions: dict[str, tuple[QualityVerdict, RecommendedAction]] = {
        "too_coarse": ("too_coarse", "expand_children"),
        "too_fine": ("too_fine", "switch_to_boundary"),
        "coverage_gap": ("coverage_gap", "reparse_region"),
        "overlap": ("overlap", "retry_same_strategy"),
        "duplicate": ("duplicate", "review_parent"),
    }
    for item in frame.source_context:
        status = str(item.get("quality_status") or item.get("parse_status") or "").strip().lower()
        if status not in source_quality_actions:
            continue
        quality_verdict, quality_action = source_quality_actions[status]
        findings.append(
            ObservationFinding(
                code=f"parse_quality_{status}",
                verdict=quality_verdict,
                severity="warning",
                subject_id=frame.subject.subject_id,
                evidence_ids=tuple(
                    str(item.get(key))
                    for key in ("member_id", "evidence_id")
                    if item.get(key)
                ),
                message=f"source interpretation is explicitly classified as {status}",
            )
        )
    uncertain_statuses = {
        "expanding",
        "quality_unknown",
        "review_required",
        "stale",
        "failed",
        "inactive",
        "historical",
        "unknown",
    }
    if critic_failed or source_statuses & uncertain_statuses:
        findings.append(
            ObservationFinding(
                code="parse_quality_uncertain",
                verdict="quality_unknown" if critic_failed else "review_required",
                severity="error",
                subject_id=frame.subject.subject_id,
                evidence_ids=tuple(
                    str(item.get("member_id") or item.get("evidence_id"))
                    for item in frame.source_context
                    if item.get("member_id") or item.get("evidence_id")
                ),
                message="source interpretation is not safe for automatic graph repair",
            )
        )
    for relation in frame.relation_context:
        if not relation.get("provenance") and not relation.get("evidence_ids"):
            findings.append(
                ObservationFinding(
                    code="relation_missing_provenance",
                    verdict="relation_unsupported",
                    severity="warning",
                    subject_id=str(relation.get("relation_id") or frame.subject.subject_id),
                    message="relation has no auditable evidence reference",
                )
            )
    if findings:
        verdict: QualityVerdict = (
            "quality_unknown"
            if critic_failed
            else quality_verdict
            if quality_verdict is not None
            else "review_required"
        )
        action: RecommendedAction = (
            "request_human_review"
            if critic_failed
            else quality_action
            if quality_action is not None
            else "review_parent"
        )
        watermark_epoch = int(time.time()) // max(60, int(watermark_expiry_seconds))
        watermark_key = str(
            stable_id(
                "maintenance_quality_watermark",
                frame.subject.workspace_id,
                frame.subject.namespace,
                frame.subject.subject_id,
                frame.subject.revision_id or "",
                frame.active_view_id or "",
                frame.active_view_version or 0,
                verdict,
                "critic_failed" if critic_failed else "deterministic",
                str(watermark_epoch),
            )
        )
        return ParseAndGraphQualityAssessment(
            assessment_id=str(stable_id("maintenance_assessment", frame.frame_id, verdict)),
            watermark_key=watermark_key,
            frame_id=frame.frame_id,
            subject=frame.subject,
            verdict=verdict,
            recommended_action=action,
            findings=tuple(findings),
            active_view_id=frame.active_view_id,
            active_view_version=frame.active_view_version,
            critic_status="failed" if critic_failed else "not_used",
            continuation_allowed=not critic_failed,
            watermark_expiry_seconds=max(60, int(watermark_expiry_seconds)),
        )
    watermark_epoch = int(time.time()) // max(60, int(watermark_expiry_seconds))
    watermark_key = str(
        stable_id(
            "maintenance_quality_watermark",
            frame.subject.workspace_id,
            frame.subject.namespace,
            frame.subject.subject_id,
            frame.subject.revision_id or "",
            frame.active_view_id or "",
            frame.active_view_version or 0,
            "adequate",
            "deterministic",
            str(watermark_epoch),
        )
    )
    return ParseAndGraphQualityAssessment(
        assessment_id=str(stable_id("maintenance_assessment", frame.frame_id, "adequate")),
        watermark_key=watermark_key,
        frame_id=frame.frame_id,
        subject=frame.subject,
        verdict="adequate",
        recommended_action="none",
        active_view_id=frame.active_view_id,
        active_view_version=frame.active_view_version,
        critic_status="not_used",
        continuation_allowed=False,
        watermark_expiry_seconds=max(60, int(watermark_expiry_seconds)),
    )


__all__ = [
    "MaintenanceObservationFrame",
    "ObservationFinding",
    "ObservationRuntimeLimits",
    "ObservationSubject",
    "ParseAndGraphQualityAssessment",
    "assess_observation_frame",
    "build_observation_frame",
]
