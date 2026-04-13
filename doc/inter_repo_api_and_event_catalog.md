# Inter-Repo API and Event Catalog

## 1. Purpose

This document defines the concrete interaction surface across:

- `kogwistar`
- `kg-doc-parser`
- `kogwistar-obsidian-sink`
- `kogwistar-llm-wiki`

It focuses on:

- commands
- APIs
- workflow entrypoints
- events
- ownership
- view mode usage via `Model[...]`

This document is intentionally implementation-oriented.

---

## 2. Architectural Rules

### 2.1 Authority

- `kogwistar` owns authoritative graph state and event history
- `kg-doc-parser` owns parsing and grounded extraction outputs
- `kogwistar-obsidian-sink` owns vault materialization behavior
- `kogwistar-llm-wiki` owns product behavior and maintenance policy

### 2.2 Interaction Modes

Only these interaction modes are allowed:

- direct function/API call
- workflow invocation
- event subscription
- explicit read/query

No repo may mutate another repo's internal state directly.

### 2.3 View Mode Rule

When a consumer does not need the full canonical model, it should consume a projected model view.

Examples:

- `Node["backend"]`
- `Node["llm"]`
- `Node["dto"]`
- `Node["frontend"]`
- `Node["sink"]`
- `Node["review"]`

Canonical models remain authoritative. Projected views are consumer contracts.

---

## 3. Command Catalog

## 3.1 `kg-doc-parser`

### Command: `parse_document`

**Owner:** `kg-doc-parser`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Parse an input source into grounded, deterministic, graph-ready artifacts.

**Input**
```python
SourceInput["backend"]
```

**Output**
```python
ParsedDocument["backend"]
```

**Notes**
- must be deterministic for identical input
- must preserve grounding
- must not write to Kogwistar directly

### Command: `repair_document_parse`

**Owner:** `kg-doc-parser`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Retry or repair parse results when source-map or extraction quality is insufficient.

**Input**
```python
ParsedDocument["backend"]
```

**Output**
```python
ParsedDocument["backend"]
```

### Command: `extract_source_native_links`

**Owner:** `kg-doc-parser`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Return source-native structure/citation/reference links derived from the source itself.

**Output**
```python
SourceNativeLinkSet["backend"]
```

---

## 3.2 `kogwistar`

### Command: `ingest_parsed_document`

**Owner:** `kogwistar`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Write parsed and grounded artifacts into graph-authoritative state.

**Input**
```python
ParsedDocument["backend"]
IngestionContext["backend"]
```

**Behavior**
- creates `Node` and `Edge`
- attaches `mentions -> groundings -> spans`
- emits `entity_events`
- lands initial artifacts in conversation-oriented namespaces or collections per app policy

**Output**
```python
IngestionResult["backend"]
```

### Command: `append_entity_event`

**Owner:** `kogwistar`  
**Called by:** `kogwistar-llm-wiki`, `kogwistar` runtime internals

**Purpose**  
Append authoritative entity mutation events.

**Input**
```python
EntityEvent["backend"]
```

**Output**
```python
AppendResult["backend"]
```

### Command: `query_graph`

**Owner:** `kogwistar`  
**Called by:** all consuming repos

**Purpose**  
Read graph entities, edges, and related artifacts.

**Input**
```python
GraphQuery["backend"]
```

**Output**
```python
GraphQueryResult["backend"]
```

### Command: `run_workflow`

**Owner:** `kogwistar`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Execute ingestion or maintenance workflows on the runtime substrate.

**Input**
```python
WorkflowInvocation["backend"]
```

**Output**
```python
WorkflowRunRef["backend"]
```

### Command: `promote_artifact`

**Owner:** `kogwistar`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Persist a promotion decision from conversation or maintenance artifacts into more durable knowledge graph state.

**Input**
```python
PromotionDecision["backend"]
```

**Output**
```python
PromotionResult["backend"]
```

---

## 3.3 `kogwistar-obsidian-sink`

### Command: `project_entity`

**Owner:** `kogwistar-obsidian-sink`  
**Called by:** usually internal consumer flow, optionally `kogwistar-llm-wiki`

**Purpose**  
Materialize one entity update into the vault.

**Input**
```python
Node["sink"] | Edge["sink"] | ProjectionEnvelope["sink"]
```

**Output**
```python
ProjectionResult["backend"]
```

### Command: `rebuild_vault`

**Owner:** `kogwistar-obsidian-sink`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Perform deterministic full rebuild from authoritative graph state.

**Input**
```python
VaultRebuildRequest["backend"]
```

**Output**
```python
VaultRebuildResult["backend"]
```

### Command: `projection_status`

**Owner:** `kogwistar-obsidian-sink`  
**Called by:** `kogwistar-llm-wiki`

**Purpose**  
Return vault sync, drift, and consumer lag status.

**Output**
```python
ProjectionStatus["dto"]
```

---

## 3.4 `kogwistar-llm-wiki`

### Command: `register_source`

**Owner:** `kogwistar-llm-wiki`  
**Called by:** UI / app API

**Purpose**  
Register a file, URL, repo, or other source into the product workspace.

**Input**
```python
SourceRegistration["dto"]
```

**Output**
```python
RegisteredSource["dto"]
```

### Command: `submit_ingestion`

**Owner:** `kogwistar-llm-wiki`  
**Called by:** UI / app API

**Purpose**  
Coordinate parse + ingest workflow for a new source.

**Input**
```python
RegisteredSource["backend"]
```

**Output**
```python
IngestionSubmissionResult["dto"]
```

### Command: `schedule_maintenance_job`

**Owner:** `kogwistar-llm-wiki`  
**Called by:** UI / app API / event handlers

**Purpose**  
Schedule a product-defined maintenance job.

**Input**
```python
MaintenanceJobRequest["backend"]
```

**Output**
```python
MaintenanceJobRef["dto"]
```

### Command: `review_promotion_candidate`

**Owner:** `kogwistar-llm-wiki`  
**Called by:** UI / app API

**Purpose**  
Accept, reject, or defer a promotion decision.

**Input**
```python
PromotionReviewDecision["dto"]
```

**Output**
```python
PromotionReviewResult["dto"]
```

---

## 4. Event Catalog

## 4.1 Authoritative events from `kogwistar`

### Event: `entity.created`

Emitted when a new node or edge is created.

**Payload**
```python
EntityCreatedEvent["backend"]
```

**Consumers**
- `kogwistar-llm-wiki`
- `kogwistar-obsidian-sink`

### Event: `entity.updated`

Emitted when a node or edge changes.

**Payload**
```python
EntityUpdatedEvent["backend"]
```

**Consumers**
- `kogwistar-llm-wiki`
- `kogwistar-obsidian-sink`

### Event: `entity.deleted`

Emitted when an entity is tombstoned or logically removed from active projection.

**Payload**
```python
EntityDeletedEvent["backend"]
```

**Consumers**
- `kogwistar-llm-wiki`
- `kogwistar-obsidian-sink`

### Event: `workflow.run.started`

**Payload**
```python
WorkflowRunStarted["backend"]
```

**Consumers**
- `kogwistar-llm-wiki`

### Event: `workflow.run.completed`

**Payload**
```python
WorkflowRunCompleted["backend"]
```

**Consumers**
- `kogwistar-llm-wiki`

### Event: `workflow.run.failed`

**Payload**
```python
WorkflowRunFailed["backend"]
```

**Consumers**
- `kogwistar-llm-wiki`

---

## 4.2 Product events from `kogwistar-llm-wiki`

### Event: `source.registered`

**Payload**
```python
RegisteredSource["backend"]
```

**Consumers**
- app UI
- orchestration layer

### Event: `maintenance.job.requested`

**Payload**
```python
MaintenanceJobRequest["backend"]
```

**Consumers**
- maintenance scheduler
- audit UI

### Event: `maintenance.job.completed`

**Payload**
```python
MaintenanceJobCompleted["backend"]
```

**Consumers**
- review UI
- promotion queue
- observability panel

### Event: `promotion.candidate.created`

**Payload**
```python
PromotionCandidate["review"]
```

**Consumers**
- review UI
- auto-promotion policy engine

### Event: `promotion.decision.recorded`

**Payload**
```python
PromotionDecision["backend"]
```

**Consumers**
- `kogwistar`
- review UI
- audit/explainability view

### Event: `wisdom.artifact.created`

**Payload**
```python
WisdomArtifact["dto"]
```

**Consumers**
- knowledge UI
- future workflow assist logic

---

## 4.3 Projection events from `kogwistar-obsidian-sink`

### Event: `projection.entity.materialized`

**Payload**
```python
ProjectionEntityMaterialized["backend"]
```

**Consumers**
- app observability UI

### Event: `projection.entity.failed`

**Payload**
```python
ProjectionEntityFailed["backend"]
```

**Consumers**
- app observability UI
- retry policy logic

### Event: `projection.rebuild.completed`

**Payload**
```python
ProjectionRebuildCompleted["backend"]
```

**Consumers**
- app UI
- deployment/ops

---

## 5. Contract Ownership

| Contract | Owner | Typical Consumer |
|---|---|---|
| canonical `Node` / `Edge` / provenance model | `kogwistar` | all repos |
| workflow invocation contract | `kogwistar` | `kogwistar-llm-wiki` |
| `ParsedDocument` contract | `kg-doc-parser` | `kogwistar-llm-wiki` |
| projection materialization contract | `kogwistar-obsidian-sink` | `kogwistar-llm-wiki` |
| maintenance request/result contracts | `kogwistar-llm-wiki` | `kogwistar`, UI |

---

## 6. View Mode Catalog

These names should be kept small and stable.

### `["backend"]`
Internal orchestration and repo-to-repo exchange.

### `["dto"]`
API-safe response/request models.

### `["frontend"]`
UI-optimized contract.

### `["llm"]`
LLM structured output response shape.


### `["llm-in"]`
LLM-parsed structured document hypergraph shape.

### `["sink"]`
Projection-safe shape for sink consumers.

### `["review"]`
Human-review-friendly presentation shape.

---

## 7. Anti-Patterns

- parser directly mutates graph
- sink mutates graph
- product repo redefines canonical model structure
- raw canonical model exposed everywhere when `Model[...]` is enough
- app-specific event types added to `kogwistar` without substrate-level reuse

---

## 8. Open Items

- exact event names and versioning convention
- whether review queue artifacts are canonical nodes or product-side records
- whether `["sink"]` should be defined centrally or only in projection-adjacent code
- exact promotion command signature
- exact workflow invocation payload shapes

---

## 9. Outcome

This catalog turns repo boundaries into concrete interfaces.

The next document should define the core end-to-end workflows using these contracts.
