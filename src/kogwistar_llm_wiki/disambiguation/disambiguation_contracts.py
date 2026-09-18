from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DisambiguationArtifactStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    STALE = "stale"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_MORE_EVIDENCE_COLLECTION = "needs_more_evidence_collection"
    CHALLENGED = "challenged"


class DisambiguationResolutionSource(StrEnum):
    NONE = "none"
    USER = "user"
    POLICY = "policy"
    NEW_EVIDENCE = "new_evidence"
    PRIOR_DECISION = "prior_decision"


class DisambiguationDecisionKind(StrEnum):
    SAME_ENTITY = "same_entity"
    DISTINCT_ENTITIES = "distinct_entities"
    AMBIGUOUS = "ambiguous"
    ILL_FORMED_CANDIDATE = "ill_formed_candidate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class DisambiguationScoreBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge_likelihood: float = Field(ge=0.0, le=1.0)
    distinction_pressure: float = Field(ge=0.0, le=1.0)
    review_priority: float = Field(ge=0.0, le=1.0)
    usage_frequency: int = Field(ge=0)
    answer_risk: float = Field(ge=0.0, le=1.0)


class DisambiguationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    workspace_id: str
    candidate_key: str
    entity_ids: tuple[str, ...] = Field(min_length=2)
    evidence_snapshot_id: str
    evidence_cutoff_ms: int
    last_reconciled_evidence_version: int = 0
    artifact_status: DisambiguationArtifactStatus = DisambiguationArtifactStatus.PENDING
    resolution_source: DisambiguationResolutionSource = DisambiguationResolutionSource.NONE
    semantic_decision: DisambiguationDecisionKind = DisambiguationDecisionKind.AMBIGUOUS
    question: str
    question_is_concrete: bool = True
    evidence_summary: str = ""
    canonical_entity_id: str | None = None
    related_entity_ids: tuple[str, ...] = Field(default_factory=tuple)
    replacement_question: str | None = None
    source_document_ids: tuple[str, ...] = Field(default_factory=tuple)
    source_span_ids: tuple[str, ...] = Field(default_factory=tuple)
    score_bundle: DisambiguationScoreBundle
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_candidate(self) -> DisambiguationCandidate:
        entity_ids = tuple(str(entity_id).strip() for entity_id in self.entity_ids if str(entity_id).strip())
        if len(entity_ids) < 2:
            raise ValueError("disambiguation candidate requires at least two entity_ids")
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError("disambiguation candidate entity_ids must be unique")
        if not str(self.question or "").strip():
            raise ValueError("disambiguation candidate requires a non-empty question")
        if self.canonical_entity_id is not None:
            canonical = str(self.canonical_entity_id).strip()
            if canonical not in entity_ids:
                raise ValueError("canonical_entity_id must be one of entity_ids")
        related_ids = tuple(str(entity_id).strip() for entity_id in self.related_entity_ids if str(entity_id).strip())
        if related_ids and any(entity_id not in entity_ids for entity_id in related_ids):
            raise ValueError("related_entity_ids must be a subset of entity_ids")
        return self


class DisambiguationEvidenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_version: int = Field(ge=0)
    question_is_concrete: bool = True
    resolves_equivalence: bool = False
    resolves_distinction: bool = False
    user_decision: DisambiguationDecisionKind | None = None
    policy_decision: DisambiguationDecisionKind | None = None
    candidate_group_size: int | None = Field(default=None, ge=0)
    replacement_question: str | None = None
    evidence_summary: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    merge_likelihood: float | None = Field(default=None, ge=0.0, le=1.0)
    distinction_pressure: float | None = Field(default=None, ge=0.0, le=1.0)
    review_priority: float | None = Field(default=None, ge=0.0, le=1.0)
    decision_challenged: bool = False
    challenge_reason: str | None = None

    @model_validator(mode="after")
    def _validate_resolution_flags(self) -> DisambiguationEvidenceUpdate:
        if self.resolves_equivalence and self.resolves_distinction:
            raise ValueError("an evidence update cannot resolve equivalence and distinction at the same time")
        if self.user_decision is not None and self.policy_decision is not None:
            raise ValueError("an evidence update can only carry one explicit decision source")
        return self


class DisambiguationReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: DisambiguationCandidate
    status: DisambiguationArtifactStatus
    resolution_source: DisambiguationResolutionSource
    semantic_decision: DisambiguationDecisionKind
    should_surface_to_user: bool
    reason: str
    new_evidence_version: int
    replacement_question: str | None = None
    challenge_required: bool = False
