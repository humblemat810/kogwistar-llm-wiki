# ADR: Iterative Maintenance Graph Construction Mode

## Status

Proposed

## Context

The current ingest path favors parse-first behavior:

1. `kogwistar-llm-wiki` registers a source.
2. `kg-doc-parser` produces document-local parsed structure.
3. `kogwistar-llm-wiki` writes initial graph artifacts.
4. Maintenance jobs refine or distill later.

This works well when the parser can produce useful structure in one pass. It is
less ideal when:

- local LLMs are slow or weak
- document structure is ambiguous
- cross-document links require a wider knowledge base
- later evidence invalidates an earlier link or parse choice
- the graph should emerge through accountable incremental steps

The product already has a maintenance lane (`MaintenanceDaemon`,
`MaintenanceWorker`, durable jobs, workflow runtime integration, and budget
ledger support). The missing concept is an operation mode where maintenance is
allowed to build and correct graph structure over time from a minimally ingested
source map.

## Decision

Introduce an iterative maintenance graph construction mode at the
`kogwistar-llm-wiki` layer.

This mode ingests the source document and source map first, writes only a
minimal graph seed, and then schedules durable maintenance jobs that propose,
validate, and apply typed graph patches.

The parser remains responsible for document-scoped parsing capability. The
maintenance mode is product policy and orchestration: it decides how slowly and
under what review rules the workspace graph should grow, link, correct, and
retract information.

## Operation Modes

`parse_first`

The current default. Run parser during ingest, write a document graph payload,
then queue follow-up maintenance.

`maintenance_first`

Register source, create source map, write a minimal document seed, then queue
graph-building maintenance. The graph emerges through validated patches.

`hybrid`

Run a lightweight parser or deterministic source-map pass, write coarse
structure, then queue maintenance to expand, correct, cross-link, and retract.

## Core Semantics

### Source Map Is Authoritative

All maintenance-created facts must remain grounded in the source map or in
already accepted graph facts with provenance.

Required provenance fields:

- `source_document_id`
- `source_span_ids` or source cluster/span pointers
- `maintenance_run_id`
- `patch_id`
- `operation_id`
- `confidence`
- `status`

### Typed Patch, Not Direct LLM Writes

LLMs may propose graph changes, but they must not directly mutate the graph.

The maintenance worker should produce a typed `MaintenancePatch`, validate it,
and only then apply it through `kogwistar` graph/runtime primitives.

Patch operations should include:

- `ADD_NODE`
- `UPDATE_NODE`
- `REMOVE_NODE`
- `ADD_EDGE`
- `REMOVE_EDGE`
- `REPLACE_EDGE`
- `ADD_PARSE_CHILD`
- `RETRACT_PARSE_CHILD`
- `ADD_CROSSLINK`
- `REMOVE_CROSSLINK`
- `MARK_AMBIGUOUS`
- `REQUEST_REVIEW`

### Patch Application Is Transactional

A patch should be applied as one unit when the backend supports it. If a patch
cannot be applied atomically, the application must still be idempotent and
replayable through stable operation ids.

### Retraction Is First-Class

Incorrect information should not be silently deleted. It should be retracted or
superseded with provenance.

Retraction records should preserve:

- what was removed or replaced
- why it was removed
- which evidence triggered the change
- which patch supersedes the previous fact

### Partial Progress Is Expected

Maintenance can accept a subset of proposed operations and route risky
operations to review. A document may be in a valid intermediate state:

- `seeded`
- `expanding`
- `needs_review`
- `stable`
- `stale`
- `superseded`

## Maintenance Job Taxonomy

Initial job kinds:

- `document_seed_graph`
- `document_expand_parse_children`
- `document_correct_parse_children`
- `document_summarize_units`
- `document_extract_entities`
- `document_propose_crosslinks`
- `document_validate_crosslinks`
- `document_retract_crosslinks`
- `document_detect_conflicts`
- `conversation_promote_to_kg`
- `graph_patch_review`
- `graph_patch_apply`

Existing job kinds such as `distill` and `execution_wisdom` remain valid.

## Conversation Lane Scope

The same mode can run inside a conversation or thread lane.

A conversation may maintain a temporary graph:

- source/evidence map
- proposed facts
- local cross-links
- candidate promotion patches
- retraction patches

Stable facts can later be promoted to workspace-level KG through the existing
promotion and review policy.

## Reuse Boundaries

Use `kogwistar` for generic mechanics:

- durable jobs
- workflow runtime
- budget ledger
- event history
- transaction mode
- recovery and repair
- versioned artifacts where applicable

Use `kg-doc-parser` for parser-local capability:

- source normalization
- OCR
- source maps
- document-local parse proposals
- pointer repair

Use `kogwistar-llm-wiki` for product policy:

- operation mode selection
- maintenance job taxonomy
- patch review thresholds
- promotion rules
- cross-document linking policy
- conversation-lane scoping

## Consequences

Benefits:

- supports slow local models
- allows graph quality to improve over time
- separates proposal from validation and application
- makes incorrect links retractable
- enables conversation-scoped temporary knowledge
- avoids requiring a perfect initial parser pass

Costs:

- more maintenance job kinds
- patch validation and application layer
- status model for partially constructed documents
- more operator/debug artifacts
- need for idempotency and retry discipline

## Alternatives Considered

Parser-first only:

Simple and fast when parsing works, but brittle for difficult documents and
weak local models.

Maintenance writes directly:

Flexible, but too risky. It makes provenance, retry, and retraction difficult
to reason about.

Full reparse on every correction:

Clean in theory, but expensive and poor for local LLMs. It also loses the
incremental audit trail that maintenance patches provide.

## Acceptance Criteria

- Ingest can select `parse_first`, `maintenance_first`, or `hybrid`.
- `maintenance_first` writes a source map and minimal document seed before any
  graph-building patch is applied.
- Maintenance proposes typed patches rather than direct graph writes.
- Patch validation checks provenance, namespace scope, idempotency, and graph
  invariants.
- Patch application is transactional when supported and idempotent otherwise.
- Retractions and supersessions are first-class patch outcomes.
- Conversation lanes can run scoped maintenance without polluting the workspace
  KG.
- Existing parse-first behavior remains available.

