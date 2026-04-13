# Maintenance Job Taxonomy

## 1. Purpose

This document defines the maintenance jobs used by `kogwistar-llm-wiki`.

These jobs are product behavior.

They run on top of the generic maintenance and workflow capability provided by `kogwistar`.

---

## 2. Terminology

### Maintenance
Operational curation and upkeep of the knowledge system.

### Reasoning
A mechanism used inside some maintenance jobs.

### Wisdom
Reusable lessons derived from execution outcomes. Not a synonym for maintenance.

---

## 3. Job Families

The maintenance subsystem is divided into these families:

- ingest follow-up
- link maintenance
- entity maintenance
- evidence maintenance
- contradiction maintenance
- synthesis maintenance
- promotion maintenance
- wisdom maintenance
- projection-request maintenance

---

## 4. Job Catalog

## 4.1 `ingest_followup`

**Purpose**  
Perform first-pass organization immediately after ingest.

**Typical trigger**
- successful document ingest
- new source landing in conversation graph

**Inputs**
- `ParsedDocument["backend"]`
- `Node["backend"]`
- `Node["llm"]` where needed

**Outputs**
- candidate links
- candidate aliases
- candidate topic assignments
- promotion candidates

**Human review**  
Usually not required for candidate generation. Required later for promotion if policy says so.

## 4.2 `source_link_normalization`

**Purpose**  
Normalize and classify source-native links extracted from documents.

**Typical trigger**
- document parse completion

**Inputs**
- source-native extracted links
- fragment structures

**Outputs**
- normalized source-native edge candidates

**Human review**  
Rare

## 4.3 `candidate_crosslink`

**Purpose**  
Find likely useful cross-links across documents, conversation artifacts, and knowledge entities.

**Typical trigger**
- ingest follow-up
- cold consolidation
- manual curation request

**Inputs**
- `Node["backend"]`
- `Node["llm"]`
- existing KG entities

**Outputs**
- candidate cross-link artifacts
- confidence and evidence references

**Human review**  
Optional for suggestion stage. Recommended before strong promotion.

## 4.4 `entity_merge_candidate`

**Purpose**  
Identify likely duplicate or alias entities.

**Typical trigger**
- new entity creation
- cold consolidation sweep

**Inputs**
- candidate entity set
- provenance overlap
- label, summary, and property views

**Outputs**
- merge candidate artifacts
- alias candidate artifacts

**Human review**  
Usually required before destructive merge.

## 4.5 `topic_assignment`

**Purpose**  
Assign entities, documents, or conversation artifacts to topic clusters or topic pages.

**Typical trigger**
- after ingest
- after promotion
- stale topic refresh

**Inputs**
- entities
- topic pages
- maintenance history

**Outputs**
- topic membership suggestions
- topic expansion suggestions

**Human review**  
Optional

## 4.6 `evidence_support_scan`

**Purpose**  
Check whether important promoted artifacts still have sufficient support.

**Typical trigger**
- periodic sweep
- after merges or source removal
- before projection refresh of key pages

**Inputs**
- promoted entities and relations
- provenance and grounding references

**Outputs**
- weak-support findings
- orphan claim findings
- support reinforcement suggestions

**Human review**  
Recommended for demotion or warning decisions.

## 4.7 `contradiction_scan`

**Purpose**  
Detect conflicting claims, summaries, or relations.

**Typical trigger**
- new promoted artifacts
- periodic cold maintenance

**Inputs**
- KG-visible claims and relations
- evidence graph
- maintenance candidates

**Outputs**
- contradiction set artifacts
- review queue items
- possible synthesis prompts

**Human review**  
Usually required.

## 4.8 `link_validation`

**Purpose**  
Re-check old inferred links for validity and usefulness.

**Typical trigger**
- staleness window reached
- related entity updated
- confidence threshold dropped

**Inputs**
- inferred link candidates
- new evidence
- affected entities

**Outputs**
- keep / weaken / reject / promote recommendation

**Human review**  
Optional depending on risk.

## 4.9 `synthesis_refresh`

**Purpose**  
Refresh derived summaries, topic pages, or cross-document synthesis artifacts.

**Typical trigger**
- many linked changes
- stale synthesis timer
- manual request

**Inputs**
- promoted entities
- source fragments
- contradiction sets
- topic memberships

**Outputs**
- updated synthesis artifact
- review diff
- projection refresh request

**Human review**  
Recommended for important public-facing pages.

## 4.10 `promotion_evaluation`

**Purpose**  
Evaluate whether a conversation or maintenance artifact is ready for KG promotion.

**Typical trigger**
- candidate creation
- manual review
- cold consolidation

**Inputs**
- candidate artifact
- evidence references
- confidence signals
- product policy thresholds

**Outputs**
- promotion candidate
- defer reason
- policy explanation

**Human review**  
Optional or required depending on policy tier.

## 4.11 `staleness_scan`

**Purpose**  
Identify stale knowledge, stale topic pages, or stale projections.

**Typical trigger**
- periodic schedule
- age-based policy

**Inputs**
- timestamps
- last support update
- projection history

**Outputs**
- stale page list
- stale entity list
- refresh suggestions

**Human review**  
Not usually needed for detection.

## 4.12 `wisdom_distillation`

**Purpose**  
Derive reusable lessons from execution outcomes.

**Typical trigger**
- workflow completed
- repeated failures or successes observed
- manual distillation request

**Inputs**
- workflow runs
- maintenance outcomes
- provenance
- error and success traces

**Outputs**
- wisdom artifact candidate
- applicability notes
- confidence

**Human review**  
Recommended.

## 4.13 `projection_refresh_request`

**Purpose**  
Decide when a visible KG change should cause sink refresh.

**Typical trigger**
- promoted entity changed
- synthesis refreshed
- title or path-affecting change

**Inputs**
- changed entity set
- projection relevance policy

**Outputs**
- targeted projection request
- rebuild request if needed

**Human review**  
Rare

---

## 5. Job Metadata Contract

Each maintenance job request should include:

```python
MaintenanceJobRequest["backend"] {
    job_type
    workspace_id
    trigger_type
    candidate_ids
    requested_by
    priority
    policy_version
}
```

Each maintenance job result should include:

```python
MaintenanceJobCompleted["backend"] {
    job_id
    job_type
    outputs
    review_required
    emitted_event_ids
    status
}
```

---

## 6. Review Tiers

### Tier 0 — Fully automatic
Low-risk organizational jobs.

Examples:
- source link normalization
- staleness detection
- projection refresh request

### Tier 1 — Suggestive automatic
System may generate candidates automatically but not apply irreversible changes.

Examples:
- cross-link candidates
- topic assignments

### Tier 2 — Human-reviewed
System proposes, human decides.

Examples:
- merge decisions
- contradiction resolutions
- important promotions
- wisdom publication

---

## 7. Input View Recommendations

| Job | Recommended Views |
|---|---|
| ingest_followup | `["backend"]`, `["llm"]` |
| candidate_crosslink | `["backend"]`, `["llm"]` |
| entity_merge_candidate | `["backend"]`, `["review"]` |
| contradiction_scan | `["backend"]`, `["llm"]`, `["review"]` |
| synthesis_refresh | `["backend"]`, `["llm"]`, `["sink"]` |
| promotion_evaluation | `["backend"]`, `["review"]` |
| wisdom_distillation | `["backend"]`, `["llm"]`, `["review"]` |
| projection_refresh_request | `["backend"]`, `["sink"]` |

---

## 8. Scheduling Guidance

### Hot-path jobs
Run soon after ingest or user action:
- ingest_followup
- source_link_normalization
- promotion_evaluation for explicit user actions

### Cold-path jobs
Run when system is not busy:
- contradiction_scan
- entity_merge_candidate
- synthesis_refresh
- staleness_scan
- wisdom_distillation

---

## 9. Anti-Patterns

- maintenance job mutates authoritative graph without emitting events
- maintenance job drops provenance
- maintenance job treats inferred link as accepted truth by default
- wisdom job stores generic reasoning traces instead of reusable lessons
- sink refresh logic mixed into every maintenance job directly

---

## 10. Outcome

This taxonomy defines the operational brain of the product.

After this, the next useful artifact is a concrete module/class map for `kogwistar-llm-wiki`.
