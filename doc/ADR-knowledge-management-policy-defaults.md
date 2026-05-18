# ADR: Knowledge Management Policy Defaults And Specialization Boundary

## Status

Accepted

## Date

2026-05-10

## Context

`kogwistar-llm-wiki` already implements a coherent knowledge-management approach:

- ingest into conversation-oriented working memory first
- keep promoted durable knowledge separate in the KG
- run background maintenance over durable queues
- derive synthesized `derived_knowledge` from promoted KG artifacts
- derive `execution_wisdom` from workflow execution history
- project only selected KG-visible state into external views such as Obsidian

This approach is largely generic and resembles a reusable default "digital brain"
or knowledge-maintenance architecture rather than a one-off wiki quirk.

At the same time, the current implementation still embeds product-specific choices
inside app code:

- exact namespace scheme such as `ws:{id}:conv:bg`, `ws:{id}:g:curated_kg`, and `ws:{id}:derived_knowledge`
- artifact vocabulary such as `candidate_link`, `promotion_candidate`,
  `promoted_knowledge`, `derived_knowledge`, and `execution_wisdom`
- promotion threshold behavior in the ingest path
- grouping logic for derived knowledge
- execution-wisdom thresholds and pattern semantics
- Obsidian projection policy and manifest conventions

We need to decide whether the existing knowledge-management approach should be
treated as a generic core default or kept mostly application-specific.

## Decision

We treat the **overall knowledge-management approach** used by `llm-wiki` as a
valid **generic default architecture**, but we do **not** treat the current
`llm-wiki` policy instances as fully generic.

The boundary is:

- `kogwistar` may own the **general policy protocol**, reusable lifecycle model,
  and conservative default implementations
- `kogwistar-llm-wiki` keeps product-specific vocabulary, thresholds, namespace
  layout, projection target choices, and domain semantics

In practice:

1. The reusable default pattern is:
   - working memory -> maintenance/review -> promoted knowledge -> derived knowledge -> wisdom -> projection
2. The current `llm-wiki` choices are not assumed to be universally correct just
   because the pattern is reusable.
3. Future policy extraction to core should move:
   - policy interfaces
   - generic default policy classes
   - reusable decision points
4. Future policy extraction should not automatically move:
   - `llm-wiki` artifact names
   - `llm-wiki` review thresholds
   - `llm-wiki` projection-specific conventions
   - `llm-wiki` maintenance-kind meanings

## Rationale

The current app architecture already shows a strong generic shape:

- conversation is used as working memory and staging
- KG is used as promoted durable knowledge
- maintenance runs in background workers
- derived knowledge is versioned synthesis over promoted knowledge
- wisdom is distilled from execution outcomes
- projection is explicitly rebuildable and non-authoritative

Those are reusable knowledge-system principles, not wiki-only hacks.

However, several current policies are still specialized enough that moving them
unchanged into core would create semantic drift:

- grouping `derived_knowledge` by label is a useful app default, but not a
  universally correct grouping rule
- `min_failure_signals=2` for execution wisdom is product policy, not substrate law
- `visibility == "projection"` and `projection_visible == True` are current app
  conventions, not universal graph semantics
- Obsidian-facing manifest behavior is a projection-product concern

So the correct abstraction target is the **policy seam**, not the current app
instance wholesale.

## Consequences

### Positive

- preserves the reusable default architecture already emerging from `llm-wiki`
- prevents app-specific semantics from being smuggled into core as if they were
  substrate truth
- gives a clean path to extract policy protocols and default implementations into
  `kogwistar`
- makes future maintenance, projection, and promotion behavior easier to test
  and compare across apps

### Negative

- some policy code will remain in `llm-wiki` longer instead of being moved
  immediately
- there will be a temporary split between:
  - generic default behavior in core
  - chosen product behavior in app
- this requires discipline in naming and documentation so "default" is not
  confused with "authoritative"

## Implementation Guidance

When extracting policy to core:

- move protocols such as promotion, visibility, grouping, replacement,
  projection-eligibility, and recovery-classification decisions
- provide conservative default policy implementations in `kogwistar`
- keep `llm-wiki` policy wiring and chosen values in the app unless intentionally
  blessed as shared defaults

Recommended classification for existing behavior:

- **Core default candidate**
  - working-memory-first ingestion
  - promotion boundary between conversation and KG
  - derived knowledge as synthesis over promoted knowledge
  - wisdom as execution-derived reusable lessons
  - projection as rebuildable non-authoritative view

- **Core protocol, app configured**
  - promotion thresholds
  - grouping rules for derived knowledge
  - wisdom extraction thresholds
  - artifact visibility policy
  - restart / recovery policy decisions

- **Remain app-specific**
  - namespace naming scheme
  - artifact vocabulary specific to `llm-wiki`
  - Obsidian-specific projection and manifest conventions
  - product meanings of maintenance kinds and review flows

## Current Code Anchors

- Working-memory to maintenance/review to promotion flow:
  [ingest_pipeline.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/ingest_pipeline.py:245)
- Promotion rule and promoted KG write:
  [ingest_pipeline.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/ingest_pipeline.py:264)
  [ingest_pipeline.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/ingest_pipeline.py:653)
- Artifact metadata policy:
  [ingest_pipeline.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/ingest_pipeline.py:760)
- Maintenance kind routing:
  [maintenance_policy.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/maintenance_policy.py:3)
- Derived knowledge synthesis policy:
  [worker.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/worker.py:276)
  [worker.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/worker.py:401)
- Execution wisdom extraction policy:
  [worker.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/worker.py:417)
- KG visibility policy:
  [namespaces.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/namespaces.py:55)
- Projection filtering behavior:
  [projection.py](C:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/projection.py:29)

## Follow-Up

Implementation note:

- The first policy-seam extraction pass is now implemented as reusable core
  code in `kogwistar/kogwistar/policy/__init__.py` plus app-owned policy wiring
  in `src/kogwistar_llm_wiki/policies.py`
- Core `kogwistar.policy` owns generic protocol/default types:
  `PromotionPolicy`, `ArtifactVisibilityPolicy`, `ProjectionEligibilityPolicy`,
  `DerivedKnowledgePolicy`, `WisdomPolicy`, `KnowledgeLifecyclePolicy`, and
  `SourceQueryDecision`
- `llm-wiki` now routes promotion, visibility, derived-knowledge, wisdom, and
  projection-eligibility decisions through explicit policy objects
- The vocabulary boundary cleanup is implemented: core defaults no longer
  classify `llm-wiki` artifact names directly, and `llm-wiki` owns
  `LlmWikiArtifactTaxonomy`

Next useful step:

- evolve the app-level promotion/review workflow without moving product-specific
  review semantics into core
- add tests when new policy choices are introduced, especially where they affect
  projection visibility or durable KG promotion
