# Maintenance Ontology & Artifact Mapping

## 1. Purpose

This document defines how maintenance should be modeled in the Kogwistar-based LLM-Wiki system.

It clarifies that maintenance is:

- **not currently a core Kogwistar graph kind**
- **not strictly a subclass of conversation**
- a semantic domain that can span multiple graph kinds

This document maps major maintenance use cases to:

- graph kind
- namespace pattern
- node / edge types
- provenance expectations
- promotion pathway

---

## 2. Core Position

### 2.1 Maintenance is not a single graph kind

Kogwistar currently gives first-class graph kind tokens such as:

- `conversation` (One instance shared by fg/bg lanes)
- `knowledge`
- `workflow`
- `wisdom` (Is a graph)

Maintenance is best treated as an **application semantic domain** layered on top of those graph kinds.

### 2.2 Maintenance is not strictly conversation

Some maintenance work can be represented as conversation-shaped artifacts, such as:

- critique
- rationale
- worker-to-worker analysis
- review preparation

But maintenance also includes non-conversation artifacts such as:

- job requests
- run records
- promotion decisions
- contradiction sets
- refresh requests

So maintenance should not be reduced to conversation alone.

### 2.3 Core vs app split

**Kogwistar**
- maintenance system capability
- workflow execution
- event sourcing
- provenance
- namespace-aware replay

**LLM-Wiki**
- maintenance policy
- artifact mapping
- relation semantics
- review thresholds
- lane semantics

---

## 3. Artifact Mapping by Use Case

| Use Case | Preferred Graph Kind | Typical Namespace | Node Type | Edge / Relation Type | Notes |
|---|---|---|---|---|---|
| Ingest follow-up request | workflow | `ws:{id}:wf:maintenance` | `maintenance_job_request` | `triggers_on` | execution coordination |
| Maintenance run record | workflow | `ws:{id}:wf:maintenance` | `maintenance_run` | `processes` | auditable execution |
| Cross-link candidate | conversation | `ws:{id}:conv:bg` | `candidate_link` | `proposes_link` | reviewable inference |
| Merge candidate | conversation | `ws:{id}:conv:bg` | `merge_candidate` | `suggests_merge` | usually reviewed |
| Credibility critique | conversation | `ws:{id}:conv:bg` | `credibility_critique` | `maintenance_critiques` | reasoning-shaped artifact |
| Reliability assessment | conversation | `ws:{id}:conv:bg` | `reliability_assessment` | `maintenance_assesses_reliability_of` | may remain background-only |
| Promotion candidate | conversation or workflow | `ws:{id}:review` or `ws:{id}:wf:maintenance` | `promotion_candidate` | `proposes_promotion_of` | app policy decides location |
| Accepted alias relation | knowledge | `ws:{id}:kg` | `entity` or `alias_relation` | `alias_of` | durable knowledge |
| Accepted contradiction marker | knowledge | `ws:{id}:kg` | `contradiction_set` | `contradicts` | durable, projection policy-dependent |
| Synthesis artifact draft | conversation | `ws:{id}:conv:bg` | `synthesis_draft` | `derived_from` | may later promote |
| Accepted synthesis artifact | knowledge | `ws:{id}:kg` | `topic_synthesis` | `summarizes` | visible candidate for projection |
| Wisdom candidate | workflow or conversation | `ws:{id}:wf:maintenance` or `ws:{id}:conv:bg` | `wisdom_candidate` | `derived_from_execution` | before distillation |
| Wisdom artifact | wisdom | `ws:{id}:wisdom` | `wisdom_artifact` | `derived_from_execution` | reusable lesson |

---

## 4. Provenance Requirements

All maintenance artifacts that become durable graph entities should preserve provenance.

Minimum expectation:

- `mentions`
- `groundings`
- `spans`

Additional useful provenance links:

- `derived_from`
- `supports`
- `contradicts`
- `supersedes`
- `maintenance_result_for`

---

## 5. Lane & Namespace Interaction

### 5.1 Foreground lane

Typical namespace:

- `ws:{workspace_id}:conv:fg`

Contains:

- user-visible interaction
- explicit user curation
- review outcomes surfaced to the user

### 5.2 Background lane

Typical namespace:

- `ws:{workspace_id}:conv:bg`

Contains:

- maintenance critique
- candidate generation
- review preparation
- consolidation dialogue or rationale

### 5.3 Message semantics

Cross-lane communication should normally be modeled as:

- creating a graph artifact in the receiver-owned namespace
- then relying on event emission / subscription

This is facilitated by sharing the same conversation engine instance between fg and bg lanes.

---

## 6. Update & Delete Behavior in Maintenance

### 6.1 Update

Maintenance update should usually mean:

- append event
- create new artifact version
- link via `supersedes` or derivation relation

### 6.2 Delete

Normal maintenance delete should usually mean:

- tombstone at the structural level
- or supersession
- or UI-level hiding

Application decisions such as `accepted` or `rejected` should be treated as app lifecycle states, not core structural delete semantics.

---

## 7. Happy Path Mapping

### 7.1 Add File → Knowledge

1. file uploaded
2. parser emits grounded document artifacts
3. ingest lands source + fragments in foreground conversation-oriented state
4. maintenance job request created in workflow namespace
5. background lane creates candidate links / merge suggestions
6. promotion candidate created
7. user or policy engine reviews
8. accepted result promoted to knowledge graph
9. Obsidian projection updates

### 7.2 Background Consolidation

1. system idle
2. maintenance workflow run starts
3. background lane produces merge / contradiction / synthesis candidates
4. selected outcomes routed to review or directly to KG per policy
5. projection refresh triggered only if KG-visible state changes

### 7.3 Contradiction Resolution

1. new relation or summary arrives
2. contradiction scan runs
3. contradiction artifact created
4. review item surfaced
5. decision recorded
6. durable contradiction marker or revised synthesis enters KG if policy allows

### 7.4 Wisdom Extraction

1. repeated runs accumulate outcomes
2. maintenance workflow identifies reusable pattern
3. wisdom candidate created
4. distilled wisdom artifact written into wisdom space
5. future workflows can consume it

---

## 8. Recommended Rule

Use this rule throughout implementation:

> Maintenance is a semantic domain.  
> Choose graph kind based on the actual artifact role:
> - execution → workflow
> - critique/rationale → conversation
> - accepted durable result → knowledge
> - reusable lesson → wisdom

---

## 9. Outcome

This ontology lets the app map use cases directly to node and edge changes without forcing maintenance into the wrong core abstraction.
