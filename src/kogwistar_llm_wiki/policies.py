from __future__ import annotations

"""llm-wiki policy instances built on top of reusable kogwistar core protocols."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from kogwistar.policy import (
    DefaultArtifactVisibilityPolicy,
    DefaultDerivedKnowledgePolicy,
    DefaultPromotionPolicy,
    DefaultProjectionEligibilityPolicy,
    DefaultWisdomPolicy,
    PromotionContext,
    PromotionDecision,
    SourceQueryDecision,
)


@dataclass(frozen=True, slots=True)
class LlmWikiArtifactTaxonomy:
    maintenance_job_request: str = "maintenance_job_request"
    candidate_link: str = "candidate_link"
    promotion_candidate: str = "promotion_candidate"
    promoted_knowledge: str = "promoted_knowledge"
    derived_knowledge: str = "derived_knowledge"
    execution_wisdom: str = "execution_wisdom"
    workflow_step_exec_entity_type: str = "workflow_step_exec"


@dataclass(frozen=True, slots=True)
class LlmWikiPromotionPolicy:
    default_accept_threshold: float = 0.95

    def decide(self, *, promotion_mode: str, auto_accept_threshold: float, metadata: Mapping[str, Any] | None = None) -> PromotionDecision:
        core = DefaultPromotionPolicy(default_accept_threshold=self.default_accept_threshold)
        return core.decide(
            PromotionContext(
                promotion_mode=promotion_mode,
                auto_accept_threshold=auto_accept_threshold,
                default_accept_threshold=self.default_accept_threshold,
                metadata=dict(metadata or {}),
            )
        )


@dataclass(frozen=True, slots=True)
class LlmWikiVisibilityPolicy:
    taxonomy: LlmWikiArtifactTaxonomy = field(default_factory=LlmWikiArtifactTaxonomy)
    _core: DefaultArtifactVisibilityPolicy = field(default_factory=DefaultArtifactVisibilityPolicy)

    def visibility_for(self, metadata: Mapping[str, Any]) -> str:
        meta = dict(metadata or {})
        artifact_kind = str(meta.get("artifact_kind") or "").strip()
        if artifact_kind == self.taxonomy.promoted_knowledge:
            return "projection" if meta.get("projection_visible") is True else "knowledge"
        if artifact_kind in {
            self.taxonomy.candidate_link,
            self.taxonomy.promotion_candidate,
        }:
            return "review"
        if artifact_kind in {
            self.taxonomy.maintenance_job_request,
            self.taxonomy.derived_knowledge,
            self.taxonomy.execution_wisdom,
        }:
            explicit = str(meta.get("visibility") or "").strip().lower()
            if explicit:
                return self._core.visibility_for(meta)
            return "internal" if artifact_kind == self.taxonomy.maintenance_job_request else "wisdom"
        return self._core.visibility_for(meta)

    def is_projection_eligible(self, metadata: Mapping[str, Any]) -> bool:
        return DefaultProjectionEligibilityPolicy(
            visibility_policy=self,
        ).is_projection_eligible(dict(metadata or {}))


@dataclass(frozen=True, slots=True)
class LlmWikiDerivedKnowledgePolicy:
    taxonomy: LlmWikiArtifactTaxonomy = field(default_factory=LlmWikiArtifactTaxonomy)
    _core: DefaultDerivedKnowledgePolicy = field(default_factory=DefaultDerivedKnowledgePolicy)

    def group_key(self, node: Any) -> str:
        return self._core.group_key(node)

    def source_query(self, *, workspace_id: str) -> SourceQueryDecision:
        query = self._core.source_query(workspace_id=workspace_id)
        return SourceQueryDecision(
            where={
                **query.where,
                "artifact_kind": self.taxonomy.promoted_knowledge,
            }
        )

    def source_where(self, *, workspace_id: str) -> dict[str, Any]:
        return self.source_query(workspace_id=workspace_id).where

    def match_where(self, *, workspace_id: str, label: str) -> dict[str, Any]:
        return {
            **self._core.match_where(workspace_id=workspace_id, label=label),
            "artifact_kind": self.taxonomy.derived_knowledge,
        }

    def build_metadata(
        self,
        *,
        workspace_id: str,
        label: str,
        source_node_ids: list[str],
        replaces_ids: list[str],
        created_at_ms: int,
        artifact_kind: str | None = None,
    ) -> dict[str, Any]:
        return self._core.build_metadata(
            workspace_id=workspace_id,
            label=label,
            source_node_ids=source_node_ids,
            replaces_ids=replaces_ids,
            created_at_ms=created_at_ms,
            artifact_kind=artifact_kind or self.taxonomy.derived_knowledge,
        )


@dataclass(frozen=True, slots=True)
class LlmWikiWisdomPolicy:
    min_failure_signals: int = 2
    taxonomy: LlmWikiArtifactTaxonomy = field(default_factory=LlmWikiArtifactTaxonomy)
    _core: DefaultWisdomPolicy = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_core", DefaultWisdomPolicy(min_failure_signals=self.min_failure_signals))

    def source_query(self, *, workspace_id: str) -> SourceQueryDecision:
        del workspace_id
        return SourceQueryDecision(
            where={
                "entity_type": self.taxonomy.workflow_step_exec_entity_type,
            }
        )

    def source_where(self, *, workspace_id: str) -> dict[str, Any]:
        return self.source_query(workspace_id=workspace_id).where

    def match_where(self, *, workspace_id: str, step_op: str) -> dict[str, Any]:
        return {
            **self._core.match_where(workspace_id=workspace_id, step_op=step_op),
            "artifact_kind": self.taxonomy.execution_wisdom,
        }

    def build_metadata(
        self,
        *,
        workspace_id: str,
        step_op: str,
        failure_count: int,
        evidence_run_ids: list[str],
        replaces_ids: list[str],
        created_at_ms: int,
        artifact_kind: str | None = None,
    ) -> dict[str, Any]:
        return self._core.build_metadata(
            workspace_id=workspace_id,
            step_op=step_op,
            failure_count=failure_count,
            evidence_run_ids=evidence_run_ids,
            replaces_ids=replaces_ids,
            created_at_ms=created_at_ms,
            artifact_kind=artifact_kind or self.taxonomy.execution_wisdom,
        )


@dataclass(frozen=True, slots=True)
class LlmWikiLifecyclePolicy:
    taxonomy: LlmWikiArtifactTaxonomy = field(default_factory=LlmWikiArtifactTaxonomy)

    def requires_provenance(self, artifact_kind: str) -> bool:
        return str(artifact_kind or "").strip() in {
            self.taxonomy.promoted_knowledge,
            self.taxonomy.derived_knowledge,
            self.taxonomy.execution_wisdom,
        }

    def replacement_ids(self, existing: list[Any]) -> list[str]:
        out: list[str] = []
        for item in existing:
            item_id = getattr(item, "id", item)
            text = str(item_id or "").strip()
            if text:
                out.append(text)
        return out


@dataclass(frozen=True, slots=True)
class LlmWikiProjectionPolicy:
    visibility: LlmWikiVisibilityPolicy = field(default_factory=LlmWikiVisibilityPolicy)

    def is_projection_eligible(self, metadata: Mapping[str, Any]) -> bool:
        return self.visibility.is_projection_eligible(metadata)


@dataclass(frozen=True, slots=True)
class LlmWikiPolicies:
    taxonomy: LlmWikiArtifactTaxonomy = field(default_factory=LlmWikiArtifactTaxonomy)
    promotion: LlmWikiPromotionPolicy | None = None
    visibility: LlmWikiVisibilityPolicy | None = None
    derived_knowledge: LlmWikiDerivedKnowledgePolicy | None = None
    wisdom: LlmWikiWisdomPolicy | None = None
    projection: LlmWikiProjectionPolicy | None = None
    lifecycle: LlmWikiLifecyclePolicy | None = None

    def __post_init__(self) -> None:
        if self.promotion is None:
            object.__setattr__(self, "promotion", LlmWikiPromotionPolicy())
        if self.visibility is None:
            object.__setattr__(
                self,
                "visibility",
                LlmWikiVisibilityPolicy(taxonomy=self.taxonomy),
            )
        if self.derived_knowledge is None:
            object.__setattr__(
                self,
                "derived_knowledge",
                LlmWikiDerivedKnowledgePolicy(taxonomy=self.taxonomy),
            )
        if self.wisdom is None:
            object.__setattr__(
                self,
                "wisdom",
                LlmWikiWisdomPolicy(taxonomy=self.taxonomy),
            )
        if self.projection is None:
            object.__setattr__(
                self,
                "projection",
                LlmWikiProjectionPolicy(visibility=self.visibility),
            )
        if self.lifecycle is None:
            object.__setattr__(
                self,
                "lifecycle",
                LlmWikiLifecyclePolicy(taxonomy=self.taxonomy),
            )


def build_default_policies() -> LlmWikiPolicies:
    return LlmWikiPolicies()


__all__ = [
    "LlmWikiPolicies",
    "LlmWikiArtifactTaxonomy",
    "LlmWikiPromotionPolicy",
    "LlmWikiVisibilityPolicy",
    "LlmWikiDerivedKnowledgePolicy",
    "LlmWikiWisdomPolicy",
    "LlmWikiLifecyclePolicy",
    "LlmWikiProjectionPolicy",
    "build_default_policies",
]
