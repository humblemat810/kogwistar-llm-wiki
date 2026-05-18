# Distillation & Maintenance - Notes on Migrating to `kogwistar` Core

**Status:** In progress
**Audience:** Kogwistar engine contributors
**Date:** 2026-04-16

---

## Background

The current `kogwistar-llm-wiki` repository implements two background capabilities on top of the Kogwistar engine:

1. **Knowledge distillation** - aggregates `promoted_knowledge` nodes from the KG into replacement `derived_knowledge` nodes
2. **Execution-history wisdom** - scans `workflow_step_exec` failure records, groups by step op, emits `execution_wisdom` patterns
3. **Maintenance worker** - a polling loop that discovers `maintenance_job_request` events and runs the distillation workflow

These are implemented as application-layer code (`worker.py`, `projection_worker.py`, `daemon.py`). Some of this logic is generic enough that it belongs in the Kogwistar engine core, just as `WorkflowRuntime` and `IndexingSubsystem` are engine-native rather than app-specific.

---

## What should move to `kogwistar` core

### 1. Generic durable job facade

**Current implementation:**
- `engine.jobs` is a generic typed facade over the existing durable `index_jobs` metastore table.
- It owns JSON payload decoding, claim/list normalization, completion marking, and retry-or-fail backoff.
- It does **not** define maintenance ontology. Apps still decide what a job means.

**Core API:**
- `engine.jobs.enqueue(...)`
- `engine.jobs.claim(...)`
- `engine.jobs.mark_done(...)`
- `engine.jobs.mark_failed(...)`
- `engine.jobs.retry_or_fail(...)`
- `engine.jobs.list(...)`

**Why this belongs in core:**
- Leasing, coalescing, retry, and terminal failure are reusable mechanics.
- Multiple application repos can use the same durable queue without touching metastore internals directly.
- Storage remains the existing `index_jobs` table; no migration is required.

---

### 2. Distillation as a first-class workflow design

**Currently (app layer):**
- `build_distillation_design()` in `maintenance_designs.py` constructs a `WorkflowDesignArtifact`
- The step resolvers (`_step_distill`, `derive_problem_solving_wisdom_from_history`) are app-specific
- Execution-history wisdom is intentionally not running as a workflow step today because reading conversation trace history from inside the runtime lane deadlocks against the runtime's own namespaced trace context

**Proposed core addition:**
- A `KnowledgeDistillationDesign` built-in workflow design similar to how the engine ships with index-job schema
- Two built-in steps wired to engine-standard operations:
  - `engine_distill_kg` - reads `promoted_knowledge` nodes, groups by label, writes configured target artifacts
  - `engine_distill_history` - reads `workflow_step_exec` failure nodes, groups by `step_op`, writes configured wisdom artifacts
- App code would only need to configure the design (threshold, namespace, etc.) or extend it

**Why:**
- The merge/deduplication logic for mentions is generic; any app doing multi-document entity resolution would need it
- The replacement-by-redirect pattern for node lifecycle is a core invariant and should remain enforced by core utilities

---

### 3. `_temporary_namespace` to engine-native context API

**Status:** Done.

`kogwistar.engine_core.engine.scoped_namespace` now owns the namespace scoping primitive. `kogwistar-llm-wiki` keeps `_temporary_namespace` only as a compatibility wrapper.

---

### 3a. Core recovery coordinator

**Status:** Done.

`kogwistar` now exposes `engine.recovery` as the generic restart recovery and
operator visibility subsystem. It owns bounded startup repair/inspection for
durable queues, projected lane rows, workflow checkpoints, run history, dead
letters, daemon health surfaces, and app-supplied output surfaces.

`kogwistar-llm-wiki` no longer owns restart recovery semantics. Its daemons pass
workspace namespaces plus app-specific probes such as projection manifest and
vault materialization state into `engine.recovery.recover_startup(...)`.

The default resume policy is intentionally inspect-only. Automatic workflow
resume requires explicit restartable markers and a caller-provided resume hook.

---

### 4. Execution-wisdom pattern recognition

**Currently (app layer):**
- `derive_problem_solving_wisdom_from_history` is an app-owned history-analysis routine that scans step exec nodes and emits `execution_wisdom`
- It uses a fixed threshold and groups only by `step_op`

**Longer-term design:**
- Core could expose a `WorkflowAnalyticsSubsystem` that provides:
  - `get_failure_patterns(workspace_id, since)` - returns aggregated failure stats
  - `get_latency_outliers(workspace_id, percentile)` - for performance-aware wisdom
  - `get_retry_chains(workspace_id)` - for resilience wisdom
- Apps call these analytics APIs and decide what to write as `execution_wisdom` artifacts
- This separates the pattern detection (core) from the wisdom authorship (app)

---

### 4a. `derived_knowledge` hosting tradeoff

**Currently (app layer):**
- `derived_knowledge` can either share the knowledge-family engine with a separate namespace (`ws:{id}:derived_knowledge`) or live on its own engine selected by `--split-derived-knowledge`
- The authoring policy is identical in both layouts; only the query/search topology changes

**What is already pinned:**
- Same-engine mode keeps one backend/query surface, which is simpler for search paths that want to read raw KG and `derived_knowledge` together
- Split-engine mode isolates storage and indexing cost, but any backend-specific search or scan that wants both surfaces must query two engines deliberately

**Why this matters before a core move:**
- If core later ships a reusable distillation template, it should not silently assume one backend topology
- The move point is the artifact authorship helper, not the hosting default; the default must stay app-configurable until the search tradeoff is settled

---

## What should stay in the application layer

| Concern | Reason to keep in app |
|---|---|
| Domain-specific step resolvers | Business logic, such as source-count thresholds |
| Promotion rules and confidence thresholds | Policy varies per deployment |
| Obsidian projection details | Sink-specific; not engine concern |
| Workflow design topology | App chooses loop vs. DAG shape |
| `WorkspaceNamespaces` naming conventions | App-defined naming scheme |

---

## Migration path

1. **Phase 1:** App implements everything. Core provides primitives (engines, runtime, subsystems).
2. **Phase 2:** Surface the existing durable queue protocol cleanly as `engine.jobs.enqueue/claim`. **Implemented as the durable job facade.**
3. **Phase 3:** Add core namespace scoping and deprecate direct app-layer namespace rebinding. **Implemented; app wrapper remains for compatibility.**
4. **Phase 4:** Add core restart recovery coordination and operator visibility. **Implemented as `engine.recovery`.**
5. **Phase 5:** Ship `KnowledgeDistillationDesign` as a built-in workflow design in Kogwistar. Apps configure, not reimplement.
6. **Phase 6:** Add `WorkflowAnalyticsSubsystem` to engine. Apps consume analytics, write `execution_wisdom`.
7. **Phase 7:** Revisit whether execution-history wisdom can move back into a runtime-native workflow after the trace-lane self-read deadlock has a core solution.

---

## Key invariants that must survive migration

- **Append-only**: any move to core must preserve redirect-based replacement semantics for node updates
- **Provenance**: every `execution_wisdom` or `derived_knowledge` node written by core must carry a `Grounding` with a real `Span` (`doc_id`, `insertion_method`, `excerpt`)
- **Dependency injection**: step resolvers must never instantiate engines internally; they receive `NamespaceEngines` or explicit engine dependencies from the caller
- **Namespace isolation**: all cross-space writes must go through a scoped namespace context (`conv_bg`, `wisdom`, `workflow_maintenance`)
