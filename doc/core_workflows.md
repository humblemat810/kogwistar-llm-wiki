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

The same product graph is also available through the interactive knowledge
workbench. Its viewing behavior is a bounded projection over these authoritative
artifacts, not a separate graph database.

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
- initial graph embedding exists
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

## 10. Workflow 9 - Interactive Knowledge Investigation

### 10.1 Goal

Allow a user to investigate a question through a bounded graph lens, receive a
cited answer, and optionally improve the graph through an explicit validated
command.

### 10.2 Shared sequence

1. user enters a question, selects anchors, or clicks an existing entity
2. the workbench requests a scoped semantic lens from the current graph
   watermark
3. the system retrieves, traverses, scores, and bounds nodes, edges, and
   hyperedges
4. the workbench shows the result with evidence and selection explanations
5. the user asks a follow-up question or requests an investigation action
6. the selected orchestration mode creates an answer and, when implemented for
   that mode, a typed graph-mutation proposal or an explicit `no_change`
   outcome
7. validation checks provenance, policy, graph revision, and conflicts
8. the user or policy accepts, rejects, or defers the proposal
9. accepted commands flow through `kogwistar` append-only events/tombstones
10. the workbench refreshes the lens from the new authoritative snapshot

The investigation history is queryable by workspace, session/conversation,
lens, source watermark, and correlation ID. It records meaningful questions,
selected entities, answer outcomes, proposals, decisions, and refreshes;
transient keystrokes and pointer movement remain client telemetry unless
explicitly saved.

An answer, proposed patch, or browser-local edit is not graph truth by itself.
Stale proposals are rejected or returned to review rather than overwriting
newer graph state.

### 10.3 Orchestration modes

- `codex_agent` target: the cockpit steward reacts to meaningful user actions,
  chooses bounded tools, can ask clarifying questions, and may propose a
  grounded multi-step investigation or graph patch through the shared command
  gate. It never writes graph state directly.
- `codex_agent` current: durable bounded cockpit worker. It can select
  read-only lens/evidence/history actions and emit a grounded node/edge patch
  proposal. The host validates and confirms separately; it has no direct graph
  writer and no first-class hyperedge command yet.
- `deterministic_workflow`: fixed host-selected transitions control retrieval,
  answer generation, proposal validation, and commit. It does not allow a
  model to bypass or choose the workflow state machine.

### 10.3.1 Implemented Codex execution lifecycle

The current Codex slice runs as an app-owned background central reasoner:

1. the browser appends an interaction request and receives `202 pending`
2. a durable job is claimed with a token and 150-second lease
3. the worker resolves the same bounded semantic lens used by deterministic mode
4. the read-only ephemeral Codex CLI answers from that lens while streamed
   activity provides progress heartbeats
5. the worker verifies lease ownership before appending the first terminal
   result and acknowledging the job
6. the browser polls the interaction artifact and ignores stale superseded UI
   requests

Process interruption leaves the request/job recoverable. A worker that becomes
silent stops renewing; if another worker later owns the job, the old result is
discarded. This slice answers or emits `no_change`; autonomous tool choice and
graph-command execution are not yet implemented.

### 10.4 Repos involved

- `kogwistar-llm-wiki`: workbench policy, lens orchestration, interaction,
  review/confirmation, and product commands
- `kogwistar`: authoritative query primitives, graph commands, events,
  revisions, provenance, and tombstone behavior
- `kg-doc-parser`: optional grounded extraction for newly supplied documents
- `kogwistar-obsidian-sink`: existing optional durable markdown projection;
  unchanged by this workflow unless a separate sink bug is reproduced

---

## 11. Open Workflow Questions

- exact graph kind placement for review queue records
- whether maintenance artifacts are canonical graph objects or product-only app records
- automatic vs manual promotion thresholds
- exact idle detection signal source

---

## 11. Outcome

These workflows define the expected operational shape of the product.

The next document should define the maintenance job taxonomy in more detail.
