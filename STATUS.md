# Status

## Refactor Plan: Distillation, Wisdom, and Cross-Repo Ownership

**Status:** In progress  
**Mode:** Runtime refactor active in `kogwistar-llm-wiki`; no cross-repo code move yet  
**Scope:** `kogwistar`, `kogwistar-llm-wiki`, and supporting docs/tests across the sibling repos

### Goal

Refactor the current maintenance/distillation stack so that:

- core `kogwistar` owns generic maintenance capability
- `kogwistar-llm-wiki` owns knowledge-maintenance policy
- "wisdom" regains the core meaning of reusable problem-solving lessons
- current label-merge aggregation is renamed or reclassified if it is really derived knowledge / synthesis
- the implementation target explicitly corrects the current misuse of `wisdom`

### Current Assessment

- [x] Durable maintenance job flow exists and is working
- [x] Namespace isolation and append-only lifecycle invariants are in place
- [x] A live app-level distillation path exists
- [x] The live label-merge path is no longer presented as core-style wisdom
- [x] Execution-history wisdom is wired into the active maintenance path
- [x] Derived knowledge is isolated from raw KG by namespace
- [x] Derived knowledge can be hosted on a separate engine from raw KG
- [x] Engine-split mode for derived knowledge is covered by tests
- [ ] Generic maintenance capability is factored cleanly enough to move into core

### Phase A: Semantic Cleanup

- [ ] Freeze terminology:
  - [ ] `knowledge` = promoted durable facts/entities
  - [ ] `derived_knowledge` or `synthesis` = cross-document consolidation
  - [ ] `wisdom` = reusable lesson for solving classes of problems
- [ ] Record the wisdom correction explicitly:
  - [ ] current live label-merge output is not treated as true wisdom
  - [ ] true wisdom target is execution-derived / maintenance-outcome-derived
- [ ] Audit docs that currently blur synthesis and wisdom
- [ ] Decide whether the current `wisdom` artifact kind should be renamed before any code move

### Phase B: Boundary Decision

- [ ] Confirm the move-down set for `kogwistar`
  - [ ] maintenance queue / claim / retry protocol
  - [ ] engine-native namespace scoping API
  - [ ] workflow analytics over execution history
  - [ ] append-only versioned artifact helpers for derived maintenance outputs
- [ ] Confirm the keep-in-app set for `kogwistar-llm-wiki`
  - [ ] grouping policy
  - [ ] promotion and review thresholds
  - [ ] maintenance job selection policy
  - [ ] artifact semantics for synthesis vs wisdom
- [ ] Decide the hosting shape for `derived_knowledge`
  - [x] minimum isolation: separate namespace from raw KG
  - [x] optional split: dedicated engine separate from raw KG engine
  - [ ] document backend/search tradeoffs for same-engine vs separate-engine hosting

### Phase C: Workflow Refactor Plan

- [ ] Split the current maintenance workflow into explicit concerns
  - [ ] synthesis / derived-knowledge workflow
  - [ ] execution-history wisdom workflow
- [ ] Make the "true wisdom" correction part of the implementation plan
  - [ ] wire an active execution-derived wisdom path
  - [ ] stop presenting synthesis output as wisdom
- [ ] Remove decorative topology:
  - [ ] either make looping real
  - [ ] or simplify the workflow to a truthful single-pass DAG
- [ ] Decide whether `_step_distill_from_history` becomes:
  - [ ] part of the default workflow
  - [ ] a separate workflow
  - [ ] or a core analytics consumer later
- [ ] Decouple engine assumptions from maintenance steps
  - [x] `derived_knowledge` writer should not be hard-wired to `engines.kg`
  - [x] `NamespaceEngines` can represent same-engine and split-engine derived-knowledge layouts

### Phase D: Test Migration Plan

- [ ] Reclassify tests by ownership
  - [ ] core capability tests move with `kogwistar`
  - [ ] app policy tests stay in `kogwistar-llm-wiki`
- [ ] Add invariant tests for the semantic split
  - [ ] synthesis is not labeled as wisdom
  - [ ] execution-history wisdom remains execution-derived
  - [ ] cross-repo queue/runtime invariants still hold
- [ ] Add topology tests for derived-knowledge hosting
  - [x] same-engine / separate-namespace mode
  - [x] separate-engine mode
  - [ ] backend-sensitive search behavior documented or pinned where practical

### Phase E: Documentation Plan

- [ ] Update architecture docs after terminology is frozen
- [ ] Update CLI/quickstart wording if artifact names change
- [ ] Keep a migration note mapping old names to new names
- [ ] Record cross-repo edit expectations anywhere contributor guidance discusses sibling repos

### Decision Gates

- [ ] Gate 1: agree on synthesis vs wisdom naming
- [ ] Gate 2: agree on what is capability vs policy
- [ ] Gate 3: agree on whether core ships a generic distillation template or only analytics + queue primitives
- [ ] Gate 4: only after the above, begin code moves across repos

### Non-Goals For This Pass

- [x] No code move into `kogwistar` yet
- [x] No artifact rename in persisted data beyond the `derived_knowledge` semantic correction
- [x] No projection behavior rewrite yet

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
  - [x] Derived-knowledge node creation is append-only: tombstone existing + write versioned new node
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
- [x] Append-only: tombstone existing derived-knowledge node for the label, then write a fresh versioned node with `replaces_ids` backlink
- [x] Execution-history analysis is active: after each maintenance workflow run, failure traces are scanned and `execution_wisdom` nodes are emitted for repeated failure patterns
- [x] Runtime workflow simplified back to a truthful synthesis/check DAG; history wisdom is emitted post-run rather than by self-reading the trace lane mid-step
- [x] Tested via focused semantic-split coverage:
  - [x] multi-document label merge now asserts `artifact_kind = derived_knowledge`
  - [x] execution-history failures now produce `execution_wisdom`
  - [x] maintenance runtime orchestration still records graph-native traces
  - [x] namespace contract now distinguishes raw KG (`ws:{id}:kg`) from derived knowledge (`ws:{id}:kg:derived`)
  - [x] split-engine hosting for derived knowledge is supported and tested

### Remaining polish (non-blocking)

- [x] `pyproject.toml` — name, version, dependencies, classifiers, `llm-wiki` script entry-point
- [x] `doc/architecture.md` — copy-paste artifacts removed (broken heading, garbled §4.5, 4× duplicated §14/15)
- [x] Integration test against real Obsidian vault — `tests/integration/test_obsidian_vault_on_disk.py` (7 tests passing, `@pytest.mark.integration`)
