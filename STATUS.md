# Status

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
  - [x] Wisdom node creation is append-only: tombstone existing + write versioned new node
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

- [x] `_step_distill` aggregates `promoted_knowledge` nodes → deduplicates mentions → writes versioned `wisdom` nodes into the `wisdom` engine
- [x] Append-only: tombstone existing wisdom node for the label, then write fresh versioned node with `replaces_ids` backlink
- [x] Tested via `test_wisdom_distillation.py` (5 tests pass)
- [x] `_step_distill_from_history`: queries `workflow_step_exec` failure nodes, groups by `step_op`, emits `execution_wisdom` nodes (append-only, tombstone+version) for patterns with ≥ 2 failure signals

### Remaining polish (non-blocking)

- [x] `pyproject.toml` — name, version, dependencies, classifiers, `llm-wiki` script entry-point
- [x] `doc/architecture.md` — copy-paste artifacts removed (broken heading, garbled §4.5, 4× duplicated §14/15)
- [x] Integration test against real Obsidian vault — `tests/integration/test_obsidian_vault_on_disk.py` (7 tests passing, `@pytest.mark.integration`)
