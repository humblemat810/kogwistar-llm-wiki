"""Bounded, provider-neutral contracts for background cross-link review."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CrosslinkEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1, max_length=160)
    node_id: str = Field(min_length=1, max_length=512)
    source_document_id: str = Field(min_length=1, max_length=512)
    source_revision_id: str = Field(min_length=1, max_length=512)
    revision_document_id: str = Field(min_length=1, max_length=512)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    excerpt: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def _ordered_range(self) -> CrosslinkEvidence:
        if self.end_char <= self.start_char:
            raise ValueError("evidence end_char must be greater than start_char")
        return self


class CrosslinkProposalOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_evidence_id: str = Field(min_length=1, max_length=160)
    right_evidence_id: str = Field(min_length=1, max_length=160)
    relation: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=600)
    supersedes_edge_id: str | None = Field(default=None, max_length=512)


class CrosslinkProposalGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1000)
    indivisible: bool = False
    operations: tuple[CrosslinkProposalOperation, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _unique_supersession_targets(self) -> CrosslinkProposalGroup:
        targets = [
            operation.supersedes_edge_id
            for operation in self.operations
            if operation.supersedes_edge_id
        ]
        if len(targets) != len(set(targets)):
            raise ValueError("a group may supersede each edge at most once")
        return self


class CrosslinkProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    groups: tuple[CrosslinkProposalGroup, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def _unique_group_ids(self) -> CrosslinkProposalResponse:
        ids = [group.group_id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("group_id values must be unique within a proposal")
        return self


class CrosslinkCriticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Literal["approve", "reject", "review"]
    explanation: str = Field(min_length=1, max_length=1000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=24)


class CrosslinkReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)


__all__ = [
    "CrosslinkCriticResponse",
    "CrosslinkEvidence",
    "CrosslinkProposalGroup",
    "CrosslinkProposalOperation",
    "CrosslinkProposalResponse",
    "CrosslinkReviewDecision",
]
