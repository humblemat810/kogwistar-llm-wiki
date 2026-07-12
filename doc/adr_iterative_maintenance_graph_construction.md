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

### Bounded Maintenance Planner

`maintenance_first` uses a durable, phase-oriented planner. A document is not
held by one worker until it is complete. Its job payload carries a typed
`maintenance_plan`, `maintenance_phase_index`, and `maintenance_round`. After
one phase succeeds, the worker persists the phase transition and requeues the
same job at the durable queue tail. This gives other documents an opportunity
to run between phases and makes resume independent of worker count.

The initial document plan is:

1. `document_seed_graph`
2. `document_parse_graph`
3. `document_propose_crosslinks`
4. `document_validate_crosslinks`

The parse phase reads the persisted source document and performs the LLM call
outside graph transactions. Only the resulting extraction and readiness
checkpoint are persisted before the next phase is queued. Crosslink phases are
proposal/review work and may not create an edge without two-sided evidence and
the normal append-only patch application rules.

The planner stops when the plan is complete, an explicit stop is requested, a
per-document round limit is reached, or the global run budget prevents another
claim. A suspended runtime continuation remains distinct from a successful
planner phase continuation; both are durable queue payloads and both re-enter
at the queue tail.

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

### Source-Attempt Fencing

Every source registration creates an immutable `source_revision` artifact
whose digest identifies the content and whose revision ID identifies that
ingestion attempt. Readiness artifacts record the stages that are safe for
maintenance, such as `source_map_seeded` or `parsed_graph_persisted`.

Every maintenance request and durable job carries the source revision ID,
source digest, and required readiness stage. Before a worker dispatches a job,
and again immediately before graph-patch application or workflow execution, it
checks that the job still targets the current revision and that the required
stage is recorded. A mismatch is not retried as business work: the job is
terminally marked failed as `stale` or `blocked`, a
`maintenance_guard_decision` artifact is appended, and the lane receives the
reason. A newer registration supersedes older queued jobs for the same source
and maintenance kind without deleting their history.

This is an application-level optimistic fence layered over Kogwistar's durable
queue lease and idempotency semantics. It prevents the common admin-triggered
retry/maintenance race and fails closed if a legacy job lacks revision
metadata. It is not a claim of a distributed transaction across the queue and
graph backends; graph mutation must remain append-only and patch application
must be idempotent.

LLMs may propose graph changes, but they must not directly mutate the graph.

The maintenance worker should produce a typed `MaintenancePatch`, validate it,
and only then apply it through `kogwistar` graph/runtime primitives.

Maintenance has two levels of vocabulary:

1. High-level maintenance intent.
2. Low-level graph patch operations.

High-level intent explains why the worker is acting. Examples:

- seed a document
- split a document or parse node
- merge duplicate nodes
- correct an incorrect parse or fact
- add a cross-document link
- retract an incorrect link
- derive a summary, entity, topic, or cross-link candidate
- refresh a summary or derived view
- promote a candidate or conversation-scoped fact into a wider scope
- retract a previous promotion
- distill durable operational knowledge into the wisdom layer
- route an ambiguous proposal for review

Low-level graph patch operations must stay aligned with `kogwistar`
semantics. `kogwistar` does not truly update or remove graph primitives.
Corrections are represented as tombstone plus add, with provenance preserved.

The low-level patch operation vocabulary should therefore be small:

- `ADD_NODE`
- `ADD_EDGE`
- `TOMBSTONE_NODE`
- `TOMBSTONE_EDGE`
- `REQUEST_REVIEW`
- `NOOP`

Higher-level actions compile into these primitives:

- `split node` compiles to `ADD_NODE` and `ADD_EDGE`
- `merge nodes` compiles to `ADD_NODE`, `ADD_EDGE`, `TOMBSTONE_NODE`, and
  `TOMBSTONE_EDGE`
- `correct fact` compiles to tombstone the incorrect node or edge and add the
  corrected replacement
- `relink` compiles to `TOMBSTONE_EDGE` plus `ADD_EDGE`
- `retract crosslink` compiles to `TOMBSTONE_EDGE`
- `derive summary`, `derive entity`, or `derive topic` compiles to `ADD_NODE`
  plus provenance edges back to the source nodes or spans
- `derive crosslink candidate` compiles to `ADD_EDGE` or a candidate link node
  plus provenance edges to both sides
- `refresh summary` compiles to tombstone the stale summary node or edge and
  add the replacement
- `promote candidate` compiles to status/scope edges when status is modeled as
  edges, or to tombstone plus replacement node when status is part of node
  identity
- `retract promotion` compiles to tombstone the status/scope edge or tombstone
  the promoted replacement node, depending on how promotion was represented
- `distill to wisdom` compiles to derived wisdom nodes and provenance edges in
  the wisdom namespace

Derivation and promotion are not low-level patch primitives. They are
maintenance intents that must lower into the same add/tombstone vocabulary as
every other graph change.

Promotion is not synonymous with the wisdom layer. Promotion moves a candidate
or scoped fact into a wider or more accepted graph scope. Wisdom extraction is
the separate `distill_to_wisdom` / `execution_wisdom` path for durable
operational lessons.

### Patch Application Is Transactional

A patch should be applied as one unit when the backend supports it. If a patch
cannot be applied atomically, the application must still be idempotent and
replayable through stable operation ids.

### Evidence Is Part Of Each Operation

`kogwistar` is already provenance-heavy, with primitives grounded to source
spans where applicable. Maintenance should not add a separate
`ATTACH_EVIDENCE` operation. Evidence belongs inside each `ADD_*`,
`TOMBSTONE_*`, or review operation payload.

If the evidence for a graph fact changes, maintenance should tombstone the old
fact or edge and add a replacement with the corrected provenance.

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

These job kinds are scheduling and policy concepts. They do not imply new
low-level graph mutations. Each job must produce validated low-level patch
operations before anything is applied.

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
