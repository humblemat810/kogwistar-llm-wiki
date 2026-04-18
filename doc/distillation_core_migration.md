# Distillation & Maintenance — Notes on Migrating to `kogwistar` Core

**Status:** Design notes / future work  
**Audience:** Kogwistar engine contributors  
**Date:** 2026-04-16

---

## Background

The current `kogwistar-llm-wiki` repository implements two background capabilities on top of the Kogwistar engine:

1. **Knowledge distillation** — aggregates `promoted_knowledge` nodes from the KG into replacement `derived_knowledge` nodes
2. **Execution-history wisdom** — scans `workflow_step_exec` failure records, groups by step op, emits `execution_wisdom` patterns
3. **Maintenance worker** — a polling loop that discovers `maintenance_job_request` events and runs the distillation workflow

These are implemented as application-layer code (`worker.py`, `projection_worker.py`, `daemon.py`). Some of this logic is generic enough that it belongs in the Kogwistar engine core — just as `WorkflowRuntime` and `IndexingSubsystem` are engine-native rather than app-specific.

---

## What should move to `kogwistar` core

### 1. Generic maintenance job protocol

**Currently (app layer):**
- `maintenance_job_request` is just a metadata convention on a graph node
- Discovery, deduplication, and completion-checking logic lives in `worker.py`
- There is no engine-level abstraction for "pending work items"

**Proposed core addition:**
- A `MaintenanceQueue` pattern analogous to `IndexingSubsystem`
- Core-defined node type: `MaintenanceJobNode` with standardized fields:
  - `job_kind` (e.g. `distillation`, `link_validation`, `contradiction_scan`)
  - `workspace_id`
  - `created_at`, `priority`, `idempotency_key`
- Core-managed status via immutable `job_status_event` nodes (append-only, same pattern as `WorkflowStepExecNode`)
- `engine.maintenance.enqueue(kind, workspace_id, payload)` API
- `engine.maintenance.claim_pending(kind)` API — returns the next unclaimed job

**Why this belongs in core:**
- The deduplication pattern (check for completion trace) is identical to the `IndexingSubsystem.reconcile_indexes` pattern already in core
- Multiple application repos (LLM-wiki, future agents) would need the same protocol
- It enables cross-repo observability via the engine's entity event log

---

### 2. Distillation as a first-class workflow design

**Currently (app layer):**
- `build_distillation_design()` in `maintenance_designs.py` constructs a `WorkflowDesignArtifact`
- The step resolvers (`_step_distill`, `derive_problem_solving_wisdom_from_history`) are app-specific
- Execution-history wisdom is intentionally not running as a workflow step today because reading conversation trace history from inside the runtime lane deadlocks against the runtime's own namespaced trace context

**Proposed core addition:**
- A `KnowledgeDistillationDesign` built-in workflow design similar to how the engine ships with index-job schema
- Two built-in steps wired to engine-standard operations:
  - `engine_distill_kg` — reads `promoted_knowledge` nodes, groups by label, writes `wisdom` nodes via `engine.wisdom.write.add_node`
  - `engine_distill_history` — reads `workflow_step_exec` failure nodes, groups by `step_op`, writes `execution_wisdom` nodes
- App code would only need to configure the design (threshold, namespace, etc.) or extend it

**Why:**
- The merge/deduplication logic for mentions is generic — any app doing multi-document entity resolution would need it
- The replacement-by-redirect pattern for node lifecycle is a core invariant; it should be enforced by core utilities, not duplicated in app code

---

### 3. `_temporary_namespace` → engine-native context API

**Currently (app layer):**
- `utils._temporary_namespace` creates a `_NamespacedEngineProxy`, temporarily rebinds all subsystem `_e` refs, and uses a per-engine `RLock`
- This is needed because `engine.namespace` is a plain mutable attribute, not a `ContextVar`

**Proposed core addition:**
- `engine.scoped_namespace(ns: str)` — a context manager that does the same CoW proxy rebinding but is owned by the engine
- Internally backed by a `contextvars.ContextVar` (for async safety) or the proxy approach (for sync safety)
- API:
  ```python
  with engine.scoped_namespace("ws:demo:conv_bg"):
      engine.write.add_node(node)  # namespace intercepted
  ```

**Why:**
- Any app using Kogwistar that needs non-default namespace scoping must re-implement this pattern
- The RLock and proxy construction belong inside the engine next to the subsystem lifecycle
- Makes the intent explicit in the engine's public API rather than a private utility function

---

### 4. Execution-wisdom pattern recognition

**Currently (app layer):**
- `derive_problem_solving_wisdom_from_history` is an app-owned history-analysis routine that scans step exec nodes and emits `execution_wisdom`
- It uses a fixed threshold (`_MIN_FAILURE_SIGNALS = 2`) and groups only by `step_op`

**Longer-term design:**
- Core could expose a `WorkflowAnalyticsSubsystem` that provides:
  - `get_failure_patterns(workspace_id, since)` — returns aggregated failure stats
  - `get_latency_outliers(workspace_id, percentile)` — for perf-aware wisdom
  - `get_retry_chains(workspace_id)` — for resilience wisdom
- Apps call these analytics APIs and decide what to write as `execution_wisdom` artifacts
- This separates the *pattern detection* (core) from the *wisdom authorship* (app)

---

### 4a. `derived_knowledge` hosting tradeoff

**Currently (app layer):**
- `derived_knowledge` can either share the KG engine with a separate namespace (`ws:{id}:kg:derived`) or live on its own engine selected by `--split-derived-knowledge`
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
| Domain-specific step resolvers | Business logic (e.g. "distill only entities with ≥ 3 sources") |
| Promotion rules and confidence thresholds | Policy varies per deployment |
| Obsidian projection details | Sink-specific; not engine concern |
| Workflow design topology | App chooses loop vs. dag shape |
| `WorkspaceNamespaces` naming conventions | App-defined naming scheme |

---

## Migration path

1. **Phase 1 (now):** App implements everything. Core provides primitives (engines, runtime, subsystems).
2. **Phase 2:** Keep the existing core queue protocol surfaced cleanly. Apps call `engine.maintenance.enqueue/claim`.
3. **Phase 3:** Add `engine.scoped_namespace()` to engine public API. Deprecate app-layer `_temporary_namespace`.
4. **Phase 4:** Ship `KnowledgeDistillationDesign` as a built-in workflow design in Kogwistar. Apps configure, not reimplement.
5. **Phase 5:** Add `WorkflowAnalyticsSubsystem` to engine. Apps consume analytics, write `execution_wisdom`.
6. **Phase 6:** Revisit whether execution-history wisdom can move back into a runtime-native workflow after the trace-lane self-read deadlock has a core solution.

---

## Key invariants that must survive migration

- **Append-only**: any move to core must preserve redirect-based replacement semantics for node updates
- **Provenance**: every `execution_wisdom` or `derived_knowledge` node written by core must carry a `Grounding` with a real `Span` (`doc_id`, `insertion_method`, `excerpt`)
- **`_deps` injection**: step resolvers must never instantiate engines internally — they receive `NamespaceEngines` via `_deps`
- **Namespace isolation**: all cross-space writes must go through a scoped namespace context (`conv_bg`, `wisdom`, `workflow_maintenance`)
