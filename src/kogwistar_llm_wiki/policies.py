"""Backward-compatible imports for LLM-Wiki policy objects."""

from .policies.rules import (
    LlmWikiArtifactTaxonomy,
    LlmWikiDerivedKnowledgePolicy,
    LlmWikiLifecyclePolicy,
    LlmWikiPolicies,
    LlmWikiProjectionPolicy,
    LlmWikiPromotionPolicy,
    LlmWikiVisibilityPolicy,
    LlmWikiWisdomPolicy,
    build_default_policies,
)

__all__ = [
    "LlmWikiArtifactTaxonomy",
    "LlmWikiDerivedKnowledgePolicy",
    "LlmWikiLifecyclePolicy",
    "LlmWikiPolicies",
    "LlmWikiProjectionPolicy",
    "LlmWikiPromotionPolicy",
    "LlmWikiVisibilityPolicy",
    "LlmWikiWisdomPolicy",
    "build_default_policies",
]
