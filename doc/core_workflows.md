# Core Workflows

## 1. Purpose

This document defines the main end-to-end workflows for the LLM-wiki product.

It answers:

- what happens
- in what order
- which repo acts
- which contracts are used
- which events are emitted
- which graph areas are affected

---

## 2. Workflow 1 — Add File and Ingest

### 2.1 Goal

User adds a file and the system turns it into grounded graph artifacts.

### 2.2 Sequence

1. User uploads file in `kogwistar-llm-wiki`
2. `kogwistar-llm-wiki` computes deterministic source identity
3. `kogwistar-llm-wiki` emits `source.registered`
4. `kogwistar-llm-wiki` calls `kg-doc-parser.parse_document`
5. `kg-doc-parser` returns `ParsedDocument["backend"]`
6. `kogwistar-llm-wiki` calls `kogwistar.ingest_parsed_document`
7. `kogwistar` appends authoritative events
8. graph projections update
9. `kogwistar` emits `entity.created` / `entity.updated`
10. `kogwistar-llm-wiki` schedules ingest-followup maintenance in the background lane

### 2.3 Repos involved

- `kogwistar-llm-wiki`
- `kg-doc-parser`
- `kogwistar`

### 2.4 Contracts used

- `SourceRegistration["dto"]`
- `ParsedDocument["backend"]`
- `ingest_parsed_document(...)`
- `entity.created`

### 2.5 Graph impact

Primary landing zone:
- foreground conversation-oriented artifacts

Possible initial artifacts:
- source document node
- fragment nodes
- source-native link edges
- extracted entity candidates
- parse metadata artifacts

### 2.6 Output state

- source is registered
- grounded parse exists
- initial graph representation exists
- maintenance follow-up is queued

---

## 3. Workflow 2 — Ingest Follow-up Maintenance

### 3.1 Goal

Run first-pass maintenance after ingestion.

### 3.2 Sequence

1. `entity.created` or ingest completion triggers maintenance
2. `kogwistar-llm-wiki` schedules `ingest_followup`
3. `kogwistar` runtime executes job
4. job reads candidate entities/fragments using:
   - `Node["backend"]`
   - `Node["llm"]` where LLM assistance is required
5. job produces:
   - candidate cross-links
   - candidate aliases
   - candidate topic memberships
   - candidate promotion records
6. `kogwistar-llm-wiki` records outputs via `kogwistar`
7. `kogwistar` emits new entity events

### 3.3 Repos involved

- `kogwistar-llm-wiki`
- `kogwistar`

### 3.4 Contracts used

- `MaintenanceJobRequest["backend"]`
- `Node["backend"]`
- `Node["llm"]`
- `MaintenanceJobCompleted["backend"]`

### 3.5 Graph impact

- conversation artifacts may gain richer structure
- maintenance artifacts are created in workflow / conversation / review-oriented spaces as policy dictates
- no automatic promotion unless policy allows

---

## 4. Workflow 3 — Review and Promotion to KG

### 4.1 Goal

Promote stable, evidence-backed artifacts into durable KG state.

### 4.2 Sequence

1. maintenance generates promotion candidate
2. `kogwistar-llm-wiki` emits `promotion.candidate.created`
3. user or policy engine reviews candidate
4. decision recorded via `review_promotion_candidate`
5. `kogwistar-llm-wiki` calls `kogwistar.promote_artifact`
6. `kogwistar` writes promotion-related events
7. KG-oriented entities become active or visible for downstream projection
8. `entity.updated` / `entity.created` emitted

### 4.3 Repos involved

- `kogwistar-llm-wiki`
- `kogwistar`

### 4.4 Contracts used

- `PromotionCandidate["review"]`
- `PromotionReviewDecision["dto"]`
- `PromotionDecision["backend"]`
- `promote_artifact(...)`

### 4.5 Graph impact

- promoted entity/edge enters stable knowledge graph view
- provenance chain from source/conversation/maintenance remains preserved

---

## 5. Workflow 4 — Incremental Obsidian Projection

### 5.1 Goal

Update vault projection when accepted knowledge changes.

### 5.2 Sequence

1. `kogwistar` emits entity event affecting projection-visible state
2. `kogwistar-obsidian-sink` consumes event
3. sink requests or receives `Node["sink"]` / `Edge["sink"]`
4. sink computes deterministic file update
5. sink writes vault changes
6. sink emits `projection.entity.materialized` or failure event
7. `kogwistar-llm-wiki` shows status in UI

### 5.3 Repos involved

- `kogwistar`
- `kogwistar-obsidian-sink`
- `kogwistar-llm-wiki`

### 5.4 Contracts used

- `entity.updated`
- `Node["sink"]`
- `Edge["sink"]`
- `ProjectionResult["backend"]`

### 5.5 Graph impact

- none on authoritative graph
- view-only projection changes

---

## 6. Workflow 5 — Cold Consolidation

### 6.1 Goal

Use idle or system-not-busy time to improve structure and reduce noise.

### 6.2 Sequence

1. idle policy decides consolidation window is available
2. `kogwistar-llm-wiki` schedules cold-path jobs
3. jobs scan recent or stale areas:
   - merge candidates
   - contradiction sets
   - weak links
   - synthesis refresh
4. jobs run via `kogwistar` workflow runtime
5. new maintenance artifacts and promotion candidates are recorded
6. optional automatic low-risk actions are applied
7. relevant events are emitted
8. sink refresh may follow if visible KG state changes

### 6.3 Repos involved

- `kogwistar-llm-wiki`
- `kogwistar`
- optionally `kogwistar-obsidian-sink`

### 6.4 Contracts used

- `MaintenanceJobRequest["backend"]`
- `WorkflowInvocation["backend"]`
- `MaintenanceJobCompleted["backend"]`

### 6.5 Graph impact

- maintenance domain grows or refines across workflow / conversation / review spaces
- selected artifacts may reach promotion queue
- no projection unless KG-visible state changes

---

## 7. Workflow 6 — Wisdom Extraction

### 7.1 Goal

Derive reusable lessons from execution outcomes, not just graph topology.

### 7.2 Sequence

1. one or more workflow runs complete
2. `kogwistar-llm-wiki` schedules `wisdom_distillation`
3. job inspects:
   - run history
   - maintenance outcomes
   - failures or successes
   - supporting provenance
4. job constructs wisdom artifact candidate
5. artifact is stored through `kogwistar`
6. event `wisdom.artifact.created` emitted
7. artifact becomes available to future workflows or UI

### 7.3 Repos involved

- `kogwistar-llm-wiki`
- `kogwistar`

### 7.4 Contracts used

- `WorkflowRunCompleted["backend"]`
- `WisdomArtifact["dto"]`

### 7.5 Graph impact

- wisdom artifact added
- must preserve derivation and provenance links

---

## 8. Workflow 7 — Full Vault Rebuild

### 8.1 Goal

Rebuild Obsidian vault deterministically from authoritative graph state.

### 8.2 Sequence

1. user or ops requests rebuild
2. `kogwistar-llm-wiki` calls `kogwistar-obsidian-sink.rebuild_vault`
3. sink re-queries all projection-visible entities
4. sink regenerates notes and canvases deterministically
5. sink emits rebuild result events or status

### 8.3 Repos involved

- `kogwistar-llm-wiki`
- `kogwistar-obsidian-sink`
- `kogwistar`

### 8.4 Contracts used

- `VaultRebuildRequest["backend"]`
- `VaultRebuildResult["backend"]`

---

## 9. Workflow 8 — Human Review Queue

### 9.1 Goal

Allow human curation of risky or important maintenance outcomes.

### 9.2 Sequence

1. maintenance job produces review-needed artifact
2. `kogwistar-llm-wiki` emits a review event or creates review item
3. UI shows `["review"]` view of candidate
4. user accepts, rejects, or defers
5. decision is recorded
6. any resulting graph mutation flows through `kogwistar`

### 9.3 Repos involved

- `kogwistar-llm-wiki`
- `kogwistar`

---

## 10. Open Workflow Questions

- exact graph kind placement for review queue records
- whether maintenance artifacts are canonical graph objects or product-only app records
- automatic vs manual promotion thresholds
- exact idle detection signal source

---

## 11. Outcome

These workflows define the expected operational shape of the product.

The next document should define the maintenance job taxonomy in more detail.
