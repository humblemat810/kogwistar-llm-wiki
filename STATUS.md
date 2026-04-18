# Status

## Refactor Status: Distillation, Wisdom, and Core Boundaries

**Status:** In progress  
**Mode:** Runtime refactor active across `kogwistar` and `kogwistar-llm-wiki`; cross-repo extraction is ongoing  
**Scope:** `kogwistar`, `kogwistar-llm-wiki`, and sibling docs/tests

### What is already true

- core maintenance plumbing exists and is working
- core queue claim / retry / completion semantics already exist in `kogwistar`
- core namespace-scoping primitive now exists and `llm-wiki` delegates to it
- core append-only replacement helper now exists and `llm-wiki` reuses it
- core reusable grouped maintenance template now exists and `llm-wiki` consumes it
- core reusable execution-wisdom template now exists and `llm-wiki` consumes it
- core workflow-step execution stats helper now exists and `llm-wiki` can reuse it
- the recent core helper extraction now lives under domain packages:
  - `kogwistar.maintenance`
  - `kogwistar.workflow`
  - `kogwistar.wisdom`
  - `kogwistar.runtime` remains a compatibility surface rather than the semantic home for those helpers
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
- generic maintenance capability is still being separated from app policy as the refactor continues

### Current shape

- `kogwistar-llm-wiki` owns:
  - document-specific grouping policy
  - promotion and review thresholds
  - maintenance job selection policy
  - artifact semantics for derived knowledge vs wisdom
- `kogwistar` already owns:
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
  - now has core failure-pattern grouping plus generic step-execution stats helpers; wisdom authorship still lives in the app
- append-only helpers for replacement derived maintenance artifacts
  - now implemented in core and reused by `llm-wiki`
- reusable grouped maintenance template for replacement artifacts
  - now implemented in core and reused by `llm-wiki`
- reusable execution-wisdom template for repeated failure patterns
  - now implemented in core and reused by `llm-wiki`
- generic workflow analytics shared by derived-knowledge and wisdom paths
  - now has a core stats helper for step-level counts and latency summaries; broader analytics variants remain optional

### Semantic buckets

#### Already core

- job queue storage and lease semantics
- claim / retry / completion lifecycle
- backend parity for in-memory, SQLite, and Postgres queue implementations
- engine-scoped namespace context handling
- repeated workflow failure grouping by `step_op`
- generic workflow-step execution stats and coarse latency summaries
- redirect-based append-only replacement for derived runtime artifacts
- reusable grouped maintenance template for replacement artifacts
- reusable execution-wisdom template for repeated failure patterns

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

- wisdom/conversation reference direction:
  - allow pointer-style references now
  - decide later whether richer structural links are allowed
- where the ARD for the reference invariant should live:
  - companion note added in `doc/wisdom_graph_reference_invariants.md`
  - use that as the implementation guardrail until a fuller ARD lands

### Invariants work to do

The following invariants are now visible, but still need either a stronger ARD
entry, a test pin, or a small implementation guard:

- [x] execution wisdom is a post-run distillation artifact, not a normal execution step
  - pinned by a core test that writes from a source graph into the wisdom graph
- [x] wisdom may guide dynamic workflow selection
  - pinned by maintenance-policy routing aliases into the execution-wisdom workflow
- foreground/background worker semantics need a deeper execution-lane decision
  - current state: namespace convention only
  - likely future shape: core lane abstraction for worker role, visibility, priority, and cancellation
  - do not turn foreground/background into a new graph kind
- [x] wisdom may meta-learn from maintenance outcomes
  - pinned by execution-wisdom derivation from failed maintenance history
- [x] derived knowledge is maintained synthesis, not merely a log
  - pinned by derived-knowledge metadata assertions in the knowledge derivation tests
- [x] conversation-to-knowledge links remain pointer-first by default
  - pinned by conversation projection tests that use deterministic pointer nodes
- conversation-to-wisdom links remain pointer-first by default
  - only widen this if a future ARD explicitly approves richer links

### Current checklist

#### Semantic cleanup

- [x] freeze terminology enough for the current implementation:
  - `knowledge` = promoted durable facts/entities
- `derived_knowledge` / `synthesis` = cross-document consolidation via replacement nodes
- `execution_wisdom` = reusable lesson for solving classes of problems
- [x] record the wisdom correction in docs and status
- [x] audit remaining docs for old synthesis/wisdom wording
- [x] keep the `wisdom` lane/name as-is for now; `execution_wisdom` is the artifact kind and the lane remains the wisdom store
- [x] core should ship generic maintenance primitives plus an overridable reusable distillation template
- [x] reusable grouped maintenance template lives in core and is consumed by `llm-wiki`
- [x] reusable execution-wisdom template lives in core and is consumed by `llm-wiki`

#### Hosting shape

- [x] separate namespace from raw KG
- [x] optional split engine for derived knowledge
- [x] CLI flag now selects same-engine vs split-engine hosting
- [x] builder path exposes the same toggle
- [x] document backend/search tradeoffs for same-engine vs split-engine hosting

#### Workflow refactor

- [x] split maintenance into explicit concerns:
  - [x] synthesis / derived-knowledge workflow
  - [x] execution-history wisdom job path
- [x] decouple derived-knowledge writes from hard-wired `engines.kg`
- [x] `NamespaceEngines` can represent same-engine and split-engine derived-knowledge layouts
- [x] expose split-engine hosting as a CLI-level toggle
- [x] remove decorative loop/check topology from the derived-knowledge maintenance workflow
- [x] extract maintenance routing policy into `maintenance_policy.py`

#### Test migration

- [x] reclassify tests by ownership
  - [x] core capability tests live with `kogwistar`
  - [x] app policy tests stay in `kogwistar-llm-wiki`
- [x] add invariant tests for the semantic split
- [x] same-engine / separate-namespace hosting is covered
- [x] separate-engine hosting is covered
- [x] same-engine hosting now pins the shared-engine derived-knowledge read path
- [x] backend-sensitive search behavior is documented and pinned for the current hosting layouts
- [x] generic workflow-step execution stats helper is in core and tested

#### Documentation

- [x] update architecture docs after terminology is sufficiently stable for the current pass
- [x] keep a migration note mapping old names to new ones
- [x] refresh CLI/quickstart wording if artifact names change again
- [x] keep cross-repo edit expectations visible in contributor guidance

### Non-goals for this pass

- [x] no projection rewrite yet
- [x] no persisted artifact rename beyond the derived-knowledge semantic correction

### Recommended next slice

Extract the remaining generic workflow analytics surface that can live cleanly in `kogwistar`, especially retry-chain summaries or latency outlier detection beyond the current step stats helper.

Why this next:
- queue semantics already live in core
- namespace scoping now lives in core
- repeated failure grouping now lives in core
- append-only replacement now lives in core
- the reusable maintenance template and execution-wisdom template already live in core
- the next work is to broaden generic analytics without pulling app policy into core

### Future Work

These items are not required for correctness. They are follow-on improvements that would make the system easier to extend, observe, or explain:

- broader workflow analytics beyond the current step stats helper
  - retry-chain summaries
  - latency outlier detection
  - hot-step / hot-path trend reporting
  - richer failure clustering beyond repeated `step_op`
- a more generic distillation template variant
  - reusable across future maintenance flows
  - only worth extracting if another app needs the same grouped replacement pattern
- final docs polish
  - shorter migration summary
  - one-page explanation of `knowledge` vs `derived_knowledge` vs `execution_wisdom`
  - cleaner “what belongs in core vs app” examples
- unresolved cross-graph reference invariants
  - decide whether wisdom may only derive from conversation/workflow artifacts or also be directly referenced back from conversation
  - decide whether conversation may hold pointers to wisdom artifacts or whether that direction should remain one-way
  - keep helpers policy-light until that invariant is explicitly written down
- worker-lane abstraction for a future AI OS shape
  - keep `conversation`, `workflow`, `knowledge`, and `wisdom` as graph semantics
  - introduce a separate execution-lane abstraction instead of a new graph kind
  - likely lane fields:
    - lane role (`interactive`, `background`, `maintenance`, `projection`, future system lanes)
    - visibility (`user_visible`, `operator_visible`, `internal_only`)
    - scheduling / QoS (`priority`, latency budget, retry policy, concurrency class)
    - control semantics (cancelable, preemptible, resumable)
    - graph access profile (which graph kinds the lane may read/write)
  - the first decision is whether lane definitions stay as app policy or become a core registry/type
  - the safest current direction is:
    - keep lane names as namespace conventions in app code
    - extract a core lane abstraction only when more than one app needs multi-lane orchestration

These are intentionally lower priority than bug fixes or correctness issues.

## Completed

- [x] Background job request/result envelopes defined in `models.py`
- [x] Projection visibility contract centralized in `namespaces.py`
- [x] Projection snapshot model and logic refined
- [x] Maintenance worker / daemon orchestrator (graph-native) implemented
  - [x] Consume workflow maintenance requests and emit run records
  - [x] Route job categories by namespace and policy
  - [x] Align maintenance with `conv_bg` and `workflow` engine partitioning
  - [x] Explicit maintenance concerns now exist:
    - [x] `maintenance.derived_knowledge.v1` for synthesis
    - [x] `execution_wisdom` jobs routed as their own maintenance kind
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

- [x] `_step_distill` now aggregates `promoted_knowledge` nodes → deduplicates mentions → writes replacement `derived_knowledge` nodes into the knowledge engine under a separate `ws:{id}:kg:derived` namespace
- [x] Append-only: prior derived-knowledge ids are redirected to the fresh replacement node, with `replaces_ids` backlink preserved
- [x] Replacement artifacts now stamp `created_at_ms` instead of an ambiguous `version_ts`
- [x] Execution-history analysis is active: after each maintenance workflow run, failure traces are scanned and `execution_wisdom` nodes are emitted for repeated failure patterns
- [x] Maintenance structure split:
  - [x] derived-knowledge uses `maintenance.derived_knowledge.v1`
  - [x] execution-wisdom uses its own maintenance kind instead of piggybacking on every synthesis run
  - [x] history-wisdom extraction stays outside the runtime step lane for now to avoid the conversation trace self-read deadlock
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
