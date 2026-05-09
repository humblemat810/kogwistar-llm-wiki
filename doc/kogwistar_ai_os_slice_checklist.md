# Kogwistar AI OS Slice Checklist

Status: Core integration baseline complete; broader AI OS work remains open
Scope: `kogwistar-llm-wiki` integration against the stable `kogwistar` OS contract

## Current integration baseline

- [x] `llm-wiki` emits lane messages through engine-level APIs
  - `send_lane_message(...)`
  - `update_lane_message_status(...)`
- [x] `llm-wiki` does not branch on concrete metastore class names
- [x] maintenance request / reply round-trip is graph-native
- [x] request message projection is visible in the worker inbox
- [x] reply message projection is visible in the foreground inbox

## Regression guardrails

- [x] request / reply flow pinned for the current maintenance path
- [x] claim / requeue / ack flow pinned through the stable engine contract
- [x] backend-agnostic parity test added for:
  - in-memory
  - SQLite / persistent local backend
  - Postgres when `KOGWISTAR_LLM_WIKI_TEST_PG_DSN` is available
- [x] persistent SQLite baseline added to prove lane-message projection survives engine reload
- [x] worker-side request-node lookup is backend-agnostic
  - normalized to a single-root filter shape that works on persistent Chroma-backed engines too

## Core dependency gaps

- [x] core knowledge policy protocol and conservative defaults
  - reusable generic policy code lives in core at `kogwistar/kogwistar/policy/__init__.py`
  - core owns `PromotionPolicy`, `ArtifactVisibilityPolicy`, `ProjectionEligibilityPolicy`, `DerivedKnowledgePolicy`, `WisdomPolicy`, `KnowledgeLifecyclePolicy`, and `SourceQueryDecision`
  - core defaults are conservative and app-agnostic; they do not know `llm-wiki` artifact names
  - `llm-wiki` keeps the configured policy instances and product vocabulary in `src/kogwistar_llm_wiki/policies.py`
- [x] policy vocabulary boundary cleanup
  - core policy defaults no longer classify `llm-wiki` artifact names directly
  - `llm-wiki` owns `LlmWikiArtifactTaxonomy`
  - derived-knowledge and wisdom policies expose typed source-query decisions
- [x] engine-level lane-message repair / rebuild API
  - `engine.repair_lane_message_projection(...)` restores projected lane-message rows from graph truth
  - app-level regression proves a worker can recover after projected lane-message rows are missing
- [x] run-registry / SSE lane lifecycle surfacing
  - runtime-sent lane messages append `worker.requested` events to the core run registry
  - existing run event polling and SSE endpoints surface the lane lifecycle event
  - lane progress reports projected lane-message rows, including projection status, for app maintenance visibility
- [x] runtime-facing durable `StepContext.send_lane_message(...)`
  - `WorkflowRuntime` injects durable lane-message sender into real resolver contexts
  - `AsyncWorkflowRuntime` mirrors the same sender/sink options through its sync runtime
  - app-level regression proves a workflow resolver can create projected lane messages through the conversation engine
- [x] core restart recovery coordinator and operator visibility
  - `engine.recovery.recover_startup(...)` owns bounded restart coordination
  - recovery reports include queues, lane rows, checkpoints, run history, dead letters, daemon health, and app output surfaces
  - default resume policy is inspect-only; auto-resume requires explicit restartable markers and a caller-provided resume hook
  - `llm-wiki` daemons now call the core recovery coordinator and only supply manifest/vault/daemon surface probes

## App-side policy still owned by `llm-wiki`

- [x] maintenance kind selection
- [x] derived-knowledge policy
- [x] execution-wisdom policy
- [x] request / reply payload shape for the wiki workflow

## Completed refactor slices

- [x] durable job queue facade over existing `index_jobs`
  - core owns generic enqueue/claim/done/fail/retry/list mechanics
  - `llm-wiki` workers use `engine.jobs` instead of direct metastore job calls
- [x] lane-message projection repair and rebuild
  - core can repair missing projected lane-message serving rows from graph truth
  - app worker recovery is covered by regression tests
- [x] runtime-facing durable lane messaging
  - real workflow step contexts can send durable lane messages through the conversation engine
- [x] run-registry and SSE lane lifecycle surfacing
  - runtime lane sends produce observable `worker.requested` run-registry events
- [x] daemon restart recovery via core coordinator
  - daemons call `engine.recovery.recover_startup(...)`
  - recovery remains at-least-once and inspect-first by default
- [x] knowledge policy protocol extraction
  - core owns generic protocols/defaults in `kogwistar.policy`
  - `llm-wiki` owns concrete taxonomy and app policy instances in `LlmWikiPolicies`
  - app call sites for ingest, maintenance derivation/wisdom, and projection eligibility route through those policy objects

## Not done yet

- [ ] richer promotion/review workflow
  - current promotion remains simple app policy, not a full review queue or adjudication workflow
- [ ] workflow checkpoint auto-resume as a scheduler-owned behavior
  - core can inspect checkpoint state, but default recovery does not auto-resume runs
- [ ] system-level scheduler / process manager
  - daemons still poll queues; there is no kernel scheduler with priority, dependency, or preemption semantics
- [ ] token/resource budget enforcement
  - runtime budget scaffolding exists in places, but no end-to-end enforced budget policy for llm-wiki jobs
- [ ] graph-native message bus over CDC oplog
  - lane messages and durable jobs exist, but there is no general topic/subscription bus
- [ ] persistent agent identity and capability registry
  - daemons do not yet register stable agent identities or advertised capabilities in the graph
- [ ] perception/sensor layer
  - no filesystem watcher, webhook receiver, RSS/email/calendar adapter, or timer-triggered ingestion loop
- [ ] workflow self-modification loop
  - `execution_wisdom` is written, but no proposal/approval/replay flow applies workflow revisions from it
- [ ] unified resource namespace / virtual filesystem
  - graph nodes, workflow runs, vault files, agents, and tools do not yet share one URI resolver
- [ ] full daemon supervisor / init system
  - startup recovery exists, but there is no cross-platform supervisor with health checks and restart policy

## Current verification anchors

- [x] `kogwistar/tests/core/test_knowledge_policy_defaults.py`
- [x] `tests/unit/test_llm_wiki_policies.py`
- [x] `tests/unit/test_projection_consistency.py`
- [x] `tests/unit/test_knowledge_derivation.py`
- [x] `tests/unit/test_lane_message_contract_integration.py`
