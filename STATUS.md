# Status

## Refactor Status: Distillation, Wisdom, and Core Boundaries

**Status:** In progress  
**Mode:** Runtime refactor active in `kogwistar-llm-wiki`; cross-repo code moves are still gated  
**Scope:** `kogwistar`, `kogwistar-llm-wiki`, and sibling docs/tests

### What is already true

- core maintenance plumbing exists and is working
- core queue claim / retry / completion semantics already exist in `kogwistar`
- core namespace-scoping primitive now exists and `llm-wiki` delegates to it
- core append-only replacement helper now exists and `llm-wiki` reuses it
- namespace isolation and append-only lifecycle invariants are in place
- the maintenance path now distinguishes:
  - `derived_knowledge` for cross-document synthesis
  - `execution_wisdom` for reusable problem-solving lessons
- `derived_knowledge` can live:
  - in the same engine as raw KG, or
  - in a separate derived-knowledge engine
- the CLI exposes `--split-derived-knowledge` so the host layout is configurable
- the split-engine choice is covered by tests

### What this refactor is correcting

- the old label-merge path is no longer being treated as core-style wisdom
- current synthesis output should be thought of as derived knowledge, not wisdom
- true wisdom remains execution-derived or outcome-derived lesson material
- generic maintenance capability still needs to be cleanly separated from app policy before any code moves into core `kogwistar`

### Current shape

- `kogwistar-llm-wiki` owns:
  - document-specific grouping policy
  - promotion and review thresholds
  - maintenance job selection policy
  - artifact semantics for derived knowledge vs wisdom
- `kogwistar` already owns or should own:
  - engine-native namespace scoping primitive
  - maintenance queue / claim / retry protocol
  - workflow analytics over execution history
  - append-only replacement helpers for generic maintenance outputs

### Core extraction candidates

The first pieces that look generic enough to expose more cleanly through `kogwistar` are:

- queue claim / retry / completion protocol
  - already implemented in core; the extraction work is to expose it more clearly to `llm-wiki`
- engine-scoped namespace context handling
  - now implemented as a core-scoped namespace primitive and reused by `llm-wiki`
- execution-history analytics over workflow step traces
  - now has a small core grouping helper; wisdom authorship still lives in the app
- append-only helpers for versioned derived maintenance artifacts
  - now implemented in core and reused by `llm-wiki`
- generic workflow analytics shared by derived-knowledge and wisdom paths
  - proposal only; not yet a core subsystem

### Semantic buckets

#### Already core

- job queue storage and lease semantics
- claim / retry / completion lifecycle
- backend parity for in-memory, SQLite, and Postgres queue implementations
- engine-scoped namespace context handling
- repeated workflow failure grouping by `step_op`
- redirect-based append-only replacement for derived runtime artifacts

#### Good core generalizations

- engine-scoped namespace context
- execution-history analytics over workflow traces
- append-only replacement helpers with redirect semantics
- generic workflow analytics for repeated failures, retries, and latency outliers

#### App policy only

- `derived_knowledge` vs `execution_wisdom` naming and semantics
- label grouping policy
- promotion/review thresholds
- exact step-op thresholds for emitting lessons
- whether an extracted lesson is written to `wisdom` or another app lane

### Open decisions

- which core abstraction should be surfaced first for `llm-wiki` reuse
- whether `derived_knowledge` stays same-engine by default or moves to split-engine by default later
- whether the current `wisdom` artifact kind should be renamed before any cross-repo code move
- whether `derive_problem_solving_wisdom_from_history` becomes:
  - part of the default workflow,
  - a separate workflow, or
  - a thin app-side consumer over core analytics
- whether core should ship only generic maintenance primitives or also a reusable distillation template

### Current checklist

#### Semantic cleanup

- [x] freeze terminology enough for the current implementation:
  - `knowledge` = promoted durable facts/entities
  - `derived_knowledge` / `synthesis` = cross-document consolidation
  - `wisdom` = reusable lesson for solving classes of problems
- [x] record the wisdom correction in docs and status
- [ ] audit remaining docs for old synthesis/wisdom wording
- [ ] decide whether persisted `wisdom` should be renamed before a code move

#### Hosting shape

- [x] separate namespace from raw KG
- [x] optional split engine for derived knowledge
- [x] CLI flag now selects same-engine vs split-engine hosting
- [x] builder path exposes the same toggle
- [ ] document backend/search tradeoffs for same-engine vs split-engine hosting

#### Workflow refactor

- [ ] split maintenance into explicit concerns:
  - [ ] synthesis / derived-knowledge workflow
  - [ ] execution-history wisdom workflow
- [x] decouple derived-knowledge writes from hard-wired `engines.kg`
- [x] `NamespaceEngines` can represent same-engine and split-engine derived-knowledge layouts
- [x] expose split-engine hosting as a CLI-level toggle
- [ ] remove any remaining decorative topology in the maintenance workflow

#### Test migration

- [ ] reclassify tests by ownership
  - [ ] core capability tests move with `kogwistar`
  - [ ] app policy tests stay in `kogwistar-llm-wiki`
- [ ] add invariant tests for the semantic split
- [x] same-engine / separate-namespace hosting is covered
- [x] separate-engine hosting is covered
- [ ] backend-sensitive search behavior still needs documentation or pinning where practical

#### Documentation

- [ ] update architecture docs after terminology is fully frozen
- [ ] keep a migration note mapping old names to new ones
- [ ] refresh CLI/quickstart wording if artifact names change again
- [ ] keep cross-repo edit expectations visible in contributor guidance

### Non-goals for this pass

- [x] no code move into `kogwistar` yet
- [x] no projection rewrite yet
- [x] no persisted artifact rename beyond the derived-knowledge semantic correction

### Recommended next slice

Document and pin the behavioral tradeoff for redirect-based replacement versus terminal tombstone, then keep `llm-wiki` as the authoring layer on top of it.

Why this next:
- queue semantics already live in core
- namespace scoping now lives in core
- repeated failure grouping now lives in core
- append-only replacement now lives in core
- the next uncertainty is semantic clarity, not mechanism

## Completed

- [x] Background job request/result envelopes defined in `models.py`
- [x] Projection visibility contract centralized in `namespaces.py`
- [x] Projection snapshot model and logic refined
- [x] Maintenance worker / daemon orchestrator (graph-native) implemented
  - [x] Consume workflow maintenance requests and emit run records
  - [x] Route job categories by namespace and policy
  - [x] Align maintenance with `conv_bg` and `workflow` engine partitioning
  - [x] Looping distillation design (`maintenance.distillation.v1`) implemented
  - [x] Verified with behavioral pinning tests
- [x] Event-driven distillation pipeline (append-only invariants)
  - [x] `MaintenanceWorker.process_pending_jobs` uses `workflow_completed` event nodes (not CRUD status)
  - [x] `_step_distill` correctly accesses `NamespaceEngines` from `_deps`
  - [x] Derived-knowledge node creation is append-only: write fresh node + redirect prior ids to it
  - [x] Fallback `Span` provenance matches kogwistar `_make_trace_span` factory
- [x] Projection worker hardened
  - [x] `_handle_projection_request` emits `projection_status_event` nodes (append-only; no CRUD mutation)
  - [x] Queue scan replaced with direct `where={"seq": next_seq}` query
- [x] Ingest pipeline cleaned up
  - [x] Hardcoded `0.95` confidence extracted as `_AUTO_ACCEPT_THRESHOLD` class constant
  - [x] Dead eager-projection stub removed; replaced with explanatory comment
  - [x] `promoted_entity_id = None` initializer restored
- [x] Test suite aligned (all 39 pass)
  - [x] `metadata.` prefix removed from all `where` clause keys
  - [x] Status assertion corrected to accept `"failure"` (from `RunFailure`)
  - [x] `test_projection_consistency` queries `projection_status_event` (append-only)
  - [x] `test_worker_runtime_orchestration` uses bare metadata keys
  - [x] Deprecated `engine.get_nodes()` → `engine.read.get_nodes()` in `projection.py`
  - [x] Deprecated `engine.tombstone_node()` → `engine.lifecycle.tombstone_node()` in test
- [x] `_temporary_namespace` rewritten with CoW proxy semantics
  - [x] `_NamespacedEngineProxy` intercepts `.namespace` reads only; real engine never mutated
  - [x] All subsystem `._e` refs (NamespaceProxy base) and `indexing.engine` temporarily rebound
  - [x] Per-engine `threading.RLock` for thread safety and reentrance
  - [x] Works for both in-memory and ChromaDB backends
  - [x] 12 unit tests pinning proxy semantics, thread safety, and nesting

### Obsidian sink projection

- [x] `ProjectionWorker` drains projection_request queue → `ProjectionManager.sync_obsidian_vault()` → `kogwistar-obsidian-sink` (fully wired)
- [x] Append-only `projection_status_event` emitted per projection attempt
- [x] Strict sequencing via `meta_sqlite` named projection state
- [x] App-level trigger: `daemon.py` (`ProjectionDaemon` + `MaintenanceDaemon`) wired with `threading.Event` stop; `__main__.py` exposes `llm-wiki daemon projection/maintenance` CLI
- [x] Integration test with real Obsidian vault on disk (`tests/integration/test_obsidian_vault_on_disk.py` — 7 tests, `@pytest.mark.integration`)

### Wisdom distillation

- [x] `_step_distill` now aggregates `promoted_knowledge` nodes → deduplicates mentions → writes versioned `derived_knowledge` nodes into the knowledge engine under a separate `ws:{id}:kg:derived` namespace
- [x] Append-only: prior derived-knowledge ids are redirected to the fresh replacement node, with `replaces_ids` backlink preserved
- [x] Replacement artifacts now stamp `created_at_ms` instead of an ambiguous `version_ts`
- [x] Execution-history analysis is active: after each maintenance workflow run, failure traces are scanned and `execution_wisdom` nodes are emitted for repeated failure patterns
- [x] Runtime workflow simplified back to a truthful synthesis/check DAG; history wisdom is emitted post-run rather than by self-reading the trace lane mid-step
- [x] Tested via focused semantic-split coverage:
  - [x] multi-document label merge now asserts `artifact_kind = derived_knowledge`
  - [x] execution-history failures now produce `execution_wisdom`
  - [x] maintenance runtime orchestration still records graph-native traces
  - [x] namespace contract now distinguishes raw KG (`ws:{id}:kg`) from derived knowledge (`ws:{id}:kg:derived`)
  - [x] split-engine hosting for derived knowledge is supported and tested
  - [x] runtime artifact replacement now redirects old ids to the new active version and exposes `created_at_ms`

### Remaining polish (non-blocking)

- [x] `pyproject.toml` — name, version, dependencies, classifiers, `llm-wiki` script entry-point
- [x] `doc/architecture.md` — copy-paste artifacts removed (broken heading, garbled §4.5, 4× duplicated §14/15)
- [x] Integration test against real Obsidian vault — `tests/integration/test_obsidian_vault_on_disk.py` (7 tests passing, `@pytest.mark.integration`)
