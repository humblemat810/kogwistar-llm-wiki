# Cross-Repository Type Contract Tracking

This ledger tracks the typing migration across the three coordinated
repositories.  A scoped zero is not a whole-repository completion claim.
Counts must be regenerated with the commands below after each structural
change.

## Branches

| Repository | Working branch | Latest typing commit |
| --- | --- | --- |
| `kogwistar` | `feat/stack-type-contracts` | `8afa3c8` |
| `kg-doc-parser` | `feat/stack-type-contracts` | `dd1e669` |
| `kogwistar-llm-wiki` | `feat/stack-type-contracts` | `df9e510` |

## Verified Scopes

| Repository | Scope | Pyright result | Ruff result | Focused tests |
| --- | --- | --- | --- | --- |
| Kogwistar | `agent/read_tools.py` | 0 errors | passed | 44 passed |
| Kogwistar | `agent/bindings.py` | 0 errors | passed | ACL/goal-agent suite passed |
| Kogwistar | `agent/control.py`, `delegation.py`, `limits.py` | 0 errors | passed | 30 agent tests passed |
| Kogwistar | `runtime/budget.py` | 0 errors | passed | 16 passed |
| Kogwistar | `runtime/rust_worker.py` protocol and JSON boundary | 0 errors, 0 warnings | passed | 21 passed |
| Kogwistar | `runtime/contract.py` workflow edge protocol | 0 errors, 0 warnings | pre-existing E402 only | 2 passed |
| Kogwistar | `runtime/routing.py` plus workflow predicate protocol | 0 errors, 0 warnings | broader legacy import check pending | route/join parity tests passed |
| Kogwistar | `runtime/runtime.py` workflow runtime boundary | 0 errors, 0 warnings | legacy E402 only | 5 focused sync contract tests passed |
| Kogwistar | `runtime/base_runtime.py`, `async_runtime.py` resolver contracts | 0 errors, 0 warnings | legacy E402 only in async/runtime peers | 69 passed, 2 skipped |
| Kogwistar | `runtime/native_contracts.py` plus sync/async native join-result adapters | 0 errors, 0 warnings | passed | 41 runtime/short-id contract tests passed |
| Kogwistar | `maintenance` artifact builders and grouped-maintenance callback protocols | 0 errors, 0 warnings across 4 files | passed | 5 maintenance artifact/template tests passed |
| Kogwistar | `server/chat_service_run_inspection.py` persisted workflow JSON boundaries | 0 errors, 0 warnings | passed | resume-contract test passed; broader server setup remains uncounted |
| Kogwistar | `server/chat_service_run_execution.py` run/state JSON contracts | 0 errors, 0 warnings | passed | 12 chat-service and workflow-lineage tests passed |
| Kogwistar | `server/chat_mcp.py` service-provider and role/namespace decorator protocols | 0 errors, 0 warnings | passed | 2 targeted MCP tests passed; recursive JSON annotations intentionally retained as legacy schema boundary |
| Kogwistar | `server/chat_service_workflow_design.py` history/projection response boundary | 0 errors, 0 warnings | passed | 6 workflow-design and lineage/ACL tests passed |
| Kogwistar | `server/chat_service_workflow_history.py` visible-delta and event payload contracts | 0 errors, 0 warnings | passed | 3 workflow-design regression tests passed |
| Kogwistar | `server/chat_service_workflow_history.py` event-row protocol boundary | 0 errors, 0 warnings | passed | 3 workflow-design regression tests passed |
| Kogwistar | `maintenance/template.py`, `grouped_artifacts.py` read-only filter protocol | 0 errors, 0 warnings | passed | maintenance-template regression passed; full source scan passed |
| Kogwistar | `server/run_registry.py` metadata-store protocol | 0 errors, 0 warnings | passed | 10 run-registry/server tests passed, 1 PostgreSQL fixture skipped |
| Kogwistar | `server/auth/db.py` auth database factory and sessionmaker boundary | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 31 auth tests passed |
| Kogwistar | `server/run_registry.py` JSON result/snapshot and event-delegate protocols plus Rust adapter | 0 errors, 0 warnings across full 262-file scan | passed (`E4,E7,E9,F`) | 1 Rust registry differential test passed, 1 PostgreSQL fixture skipped |
| Kogwistar | `server/chat_service_workflow_design.py` mutation/undo/redo JSON responses | 0 errors, 0 warnings | passed | 3 workflow-design regression tests passed |
| Kogwistar | `server/chat_mcp.py` generic role/namespace decorator protocol and `mcp_tools.py` naming cleanup | 0 errors, 0 warnings | passed | 2 MCP regression tests passed; server Ruff E4/E7/E9/F clean |
| Kogwistar | `engine_core/in_memory_meta.py` metadata and projection JSON boundaries | 0 errors, 0 warnings | passed | 22 passed, 2 warnings |
| Kogwistar | `typing_interfaces.py` plus engine subsystem protocol surface | 0 errors in protocol file; engine reduced to 44 | passed | fake backend smoke passed; optional Chroma unavailable locally |
| Kogwistar | ACL read/write protocol forwarding and backend capability attributes | engine reduced to 34 errors | passed | 42 ACL tests passed; 1 unrelated fixture-helper failure |
| Kogwistar | `engine_core/engine.py` and optional projection capability protocol | 0 errors, 0 warnings | legacy E402 in existing monolithic engine import layout | 70 passed, 8 skipped; Chroma unavailable locally; 1 unrelated ACL fixture-helper failure |
| Kogwistar | `conversation/models.py`, `conversation_context.py`, `memory_retriever.py` | 0 errors, 0 warnings | passed | 9 passed, 2 warnings |
| Kogwistar | `conversation/resolvers.py` state boundary and retrieval fallback | 44 errors, 0 warnings | passed | 18 passed, 8 warnings |
| Kogwistar | `conversation/conversation_orchestrator.py`, `conversation/tool_runner.py`, `engine_core/types.py` | 0 errors, 0 warnings | passed | 18 passed, 8 warnings |
| Kogwistar | `conversation/agentic_answering.py` | 0 errors, 0 warnings | passed | 19 passed; Chroma/real-LLM cases unavailable locally |
| Kogwistar | `conversation/service.py` | 0 errors, 0 warnings | passed | fake cancellation/causality paths passed; Chroma unavailable locally |
| Kogwistar | conversation facade and orchestrator entry points | 0 errors, 0 warnings | passed | focused conversation cases collected; optional backend cases skipped locally |
| Kogwistar | GraphKnowledgeEngine ACL facade | 0 errors, 0 warnings | passed | 38 ACL tests passed; Chroma unavailable and PostgreSQL fixtures skipped locally |
| Kogwistar | `messaging/service.py`, lane-message store protocol and projection records | 0 errors, 0 warnings across 3 messaging files | passed | 27 lane-message, visibility, projection-rebuild, and metastore-contract tests passed |
| Kogwistar | `runtime/checkpointed_projection.py` | 0 errors, 0 warnings | passed | 6 checkpoint, CAS, failure-preservation, and bounded-tail tests passed |
| Kogwistar | `runtime/telemetry.py`, `runtime/models.py`, `runtime/__init__.py` | 0 errors, 0 warnings | passed | 12 budget/projection regression tests passed |
| Kogwistar | `engine_core/embedding_profile.py` JSON/profile boundary | 0 errors, 0 warnings | selected check has legacy resolver findings outside this slice | 16 passed, 36 skipped |
| Kogwistar | `engine_core/in_memory_backend.py` sync/async projection adapters | 0 errors, 0 warnings | passed | 92 passed, 24 skipped; 6 Chroma cases unavailable without `chromadb` |
| Kogwistar | `engine_core/engine_sqlite.py` SQLite and projection JSON boundaries | 0 errors, 0 warnings | passed | 17 passed, 2 skipped; PostgreSQL fixture unavailable |
| Kogwistar | `engine_core/subsystems/read.py` graph result and adapter boundaries | 0 errors, 0 warnings | passed | 38 passed, 2 skipped, 6 deselected |
| Kogwistar | `engine_core/subsystems/write.py`, `typing_interfaces.py` write and backend protocol boundaries | 0 errors, 0 warnings | passed | 35 passed, 7 skipped, 9 deselected; Chroma unavailable locally |
| Kogwistar | `engine_core/postgres_backend.py` PostgreSQL event, async-result, and vector-buffer boundaries | 0 errors, 0 warnings | passed | 4 focused tests passed, 1 optional `pgvector` test unavailable locally, 1 PostgreSQL fixture skipped |
| Kogwistar | `runtime/resolvers.py` resolver wrapper, sandbox input, and async resolver contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 85 passed, 3 skipped |
| Kogwistar | package introspection, ACL metadata validators, and compression workflow return contract | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 191 passed, 2 skipped; 3 optional/fixture failures |
| Kogwistar | historical search facade signatures and `similarity_threshold` protocol forwarding | 0 errors, 0 warnings across engine, read, ACL, and shared protocol files | touched-file check passed; legacy engine E402 remains outside this slice | first search normalization test passed; second fake-backend test hung locally before completion |
| Kogwistar | `_rust_bridge.py` native JSON-string extension protocol and JSON result narrowing | 0 errors, 0 warnings | passed | 20 Rust/API parity tests passed |
| Kogwistar | `engine_core/rust_postgres_session.py` native PostgreSQL JSON/session boundary | 0 errors, 0 warnings | passed | 33 PostgreSQL integration tests skipped because the local fixture is unavailable |
| Kogwistar | DiskCache ignored positional dependency boundary | 0 errors, 0 warnings | passed | local-lambda regression and fake candidate tests passed; PyPy 3.11 GitHub job passed on `1a01a73` |
| Kogwistar | `engine_core/engine_postgres_meta.py` async/sync engine mode boundaries | 0 errors, 0 warnings | passed | 19 passed, 1 skipped; PostgreSQL fixture unavailable locally |
| Kogwistar | `workers/async_index_job_worker.py` generic await and queue-value boundaries | 0 errors, 0 warnings | passed | 8 passed, 2 existing warnings |
| Kogwistar | `runtime/perf_profile.py` benchmark protocol and JSON-report boundaries | 0 errors, 0 warnings | passed | 15 tests collected, all environment-gated/skipped locally |
| Kogwistar | `server/chat_service_run_execution.py`, `chat_service_shared.py`, `run_registry.py` execution and telemetry protocols | 0 errors, 0 warnings | passed | 14 passed, 7 skipped; 9 Chroma-dependent setup errors locally |
| Kogwistar | `conversation` full scoped package | 0 errors, 0 warnings across 19 files | four pre-existing Ruff findings remain in `agentic_answering_design.py` and `conversation_context.py` | 14 focused workflow/agentic tests passed; Chroma/real-LLM cases remain environment-gated |
| Kogwistar | `conversation/policy.py` policy hooks and backend JSON narrowing | 0 errors, 0 warnings | passed (`E4,E7,E9,F`) | 4 passed, 17 skipped |
| Kogwistar | `engine_core/subsystems/extract.py` and `llm_tasks/default_provider.py` | 0 errors, 0 warnings | passed | provider/structured-output tests passed; Chroma-dependent extraction tests unavailable locally |
| Kogwistar | `server/bootstrap.py` | 0 errors, 0 warnings | passed | 4 bootstrap tests passed |
| Kogwistar | `server/auth` | 0 errors, 0 warnings | passed | 39 auth/integration tests passed |
| Kogwistar | `server/chat_service.py`, `chat_service_shared.py`, `chat_service_conversation_queries.py`, `chat_service_run_execution.py` | 0 errors, 0 warnings | passed | async event suite skipped because Chroma/Postgres fixtures are unavailable locally |
| Kogwistar | `conversation/policy.py`, `server/resources.py`, `server/chat_service_run_inspection.py`, `shortids.py`, `utils/log.py` | 0 errors, 0 warnings | passed for focused files; legacy E402 remains in `utils/log.py` | focused compatibility checks passed; backend-dependent tests remain environment-gated |
| Kogwistar | `kogwistar` full source scan | 0 errors, 0 warnings after `b081654` | focused changed-file Ruff `E7,E9,F` passed; legacy `E402` remains in monolithic runtime imports | 78 runtime/server tests passed, 2 skipped; full CI remains unverified for this SHA |
| KG Doc Parser | `workflow_ingest/clients.py`, `demo_harness.py` | 0 errors | passed | 6 passed, 2 skipped |
| KG Doc Parser | `workflow_ingest/handlers.py` | 0 errors | passed | resolver/demo suites passed |
| KG Doc Parser | `workflow_ingest/serialization.py` | 0 errors | passed | serialization callers covered |
| KG Doc Parser | `workflow_ingest/service.py` | 0 errors | passed | 18 resolver tests passed |
| KG Doc Parser | `workflow_ingest` (full scoped package) | 0 errors | passed for touched modules | focused run reached all selected cases but was stopped during shutdown; not counted as a pass |
| KG Doc Parser | `kg_doc_parser` full source scan | 0 errors, 0 warnings across 40 files | 160 legacy ANN findings remain | full Pyright scan completed; runtime/integration coverage remains separate |
| KG Doc Parser | `workflow_ingest/providers.py` structured-provider schema boundary | 0 errors, 0 warnings | passed | provider settings/token-budget tests passed with vendored Core on `PYTHONPATH` |
| KG Doc Parser | `workflow_ingest/clients.py` resume argument protocol | 0 errors, 0 warnings | passed | unsupported server-resume contract passed; optional Chroma case skipped |
| KG Doc Parser | `utils/langchain.py` callback and generation-variant contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | dependency-path import check passed |
| KG Doc Parser | `ocr.py` and `pdf2png.py` callback, loader, and conversion contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | PDF suite 7 passed; OCR workflow cases passed; 2 legacy fixture/import failures |
| LLM-Wiki | ingestion/parsing/workbench targeted scope | 0 errors | passed | 14 passed, 1 deselected |
| LLM-Wiki | `src/kogwistar_llm_wiki` full scope | 0 errors, 0 warnings across 181 files | targeted groups passed; full runtime suite pending | Pyright clean |
| LLM-Wiki | maintenance observation audit and graph-space logical-reference protocols | 0 errors, 0 warnings across 2 touched files | passed (`ANN,E4,E7,E9,F`) | targeted process executed 12 tests before shutdown hang; not counted as a completed pass |
| LLM-Wiki | diagnostics, remote embedding, trace sink, and memory boundaries | 0 errors, 0 warnings across full `src` scan | passed (`E4,E7,E9,F`) | 28 passed |
| LLM-Wiki | vLLM multimodal message/content wire contracts | 0 errors, 0 warnings | passed (`E4,E7,E9,F`) | 44 passed, 3 skipped |
| LLM-Wiki | standalone embedding-service health, readiness, capability, and representation responses | 0 errors, 0 warnings | passed (`E4,E7,E9,F`) | 21 passed |
| LLM-Wiki | maintenance filter composition and grouped-template boundary | 0 errors, 0 warnings across 3 changed files | passed | 30 maintenance orchestration tests passed |
| LLM-Wiki | normal local CI-marked suite with vendored paths configured | not applicable | passed | 1,044 passed, 8 skipped, 178 deselected in 25:41 |

## Remaining Work

- Complete strict `ANN` annotation coverage in Kogwistar Core (`1,288`
  findings) and KG Doc Parser (`160` findings), prioritizing public protocol
  boundaries and callback surfaces over mechanical private helpers.
- Run the full relevant test and CI gates for each pushed slice; the latest
  Core feature-branch SHA `8afa3c8` currently has no GitHub workflow run
  attached (`runs=0`).

## Quantified Progress

Current production-source annotation snapshot (`ruff check ... --select ANN`):

| Repository | Pyright | Strict ANN findings | Interpretation |
| --- | ---: | ---: | --- |
| Kogwistar | 0 errors, 0 warnings across 262 files | 1,288 | remaining Core annotation/protocol migration |
| KG Doc Parser | 0 errors, 0 warnings across 40 files | 160 | remaining legacy parser annotations |
| LLM-Wiki | 0 errors, 0 warnings across 181 files | 0 in `src/` | production source is strict-ANN clean |

These counts are not a percentage. They are the denominator needed for a
reproducible percentage after the migration policy defines which generated,
benchmark, and test files are in scope. Full CI status is tracked separately;
local Pyright and Ruff success cannot substitute for remote CI.

The current strict production-source backlog is `1,448` findings: Kogwistar
`1,288`, KG Doc Parser `160`, and LLM-Wiki `0` in `src/`. The previous
recorded total was `1,669`; the reductions came from completed Core and parser
contract slices, not from excluding files.

For the Kogwistar full-source Pyright backlog, the first reproducible
source-only baseline was 194 errors. The last reproducible scan before
`1ffdfd4` reported 180 errors, concentrated in the runtime JSON boundaries.
The full source-only scan after `b081654` reports 0 errors and 0 warnings.
This is a backlog metric only; it does not claim that runtime behavior is
fully verified, and it does not count Ruff findings or unverified CI as
completed work. A slice is counted only after its full-scope scan is
regenerated.

- Current measured backlog: KG Doc Parser `workflow_ingest` and the full
  `kg_doc_parser` source scan have `0` Pyright errors and `0` warnings;
  the parser-wide Ruff scan currently has `194` legacy findings;
  Kogwistar full source-only scan after `b081654` measures `0` errors and `0`
  warnings. This is a typing measurement,
  not a passing runtime gate; optional backend fixtures and full CI remain
  separately unverified.
The latest runtime, maintenance, ontology, and PostgreSQL slices are clean; the remaining
  work is full runtime/CI verification and legacy Ruff cleanup, not Pyright
  diagnostics.
  The conversation
  resolver, cache-wrapper, orchestration, retrieval, agentic-answering, and
  service slices are each measured at `0` Pyright errors and `0` warnings;
  the full conversation package is now `0` Pyright errors across 19 files,
  down from `300` before the conversation typing work began. Four Ruff
  findings remain in legacy files outside the focused slice.
  These are measured scopes, not a claim that the whole stack is complete.
- [x] Run and record the current full Pyright count for the parser
      `workflow_ingest` scope.
- [x] Remove parser `workflow_ingest` errors, grouped by module and protocol
      boundary rather than by individual diagnostic.
- [x] Reduce the Kogwistar full-source Pyright backlog by package, including
      the final PostgreSQL engine boundary.
- [x] Audit all LLM-Wiki packages beyond the current targeted scope.
- [x] Remove the measured LLM-Wiki backlog of 30 errors, grouped by policy,
      transport/protocol, JSON boundaries, and model contracts.
- [x] Regenerate the full Kogwistar source-only scan after `1ffdfd4`.
- [ ] Run full local CI for each repository with the documented dependency
      paths and `-p no:cacheprovider` where appropriate.
- [ ] Verify PyPy 3.11 and CPython 3.12-3.14 compatibility after type changes.
- [ ] Observe GitHub Actions for each pushed exact SHA before treating a slice
      as remotely verified.
- [ ] Reconcile this ledger after every commit or rebase.

## Remote Verification Status

- Kogwistar run `37939704217` completed successfully for exact SHA
  `1ffdfd42f19614c750be31161edb4876302e3294`. Required CPython 3.12-3.14,
  PyPy 3.11, lint, Rust, SQLite invariants, and native-wheel smoke jobs passed.
  The PyPy 3.12 beta job failed as an explicitly non-blocking best-effort job.
- Kogwistar typing commit `8848b26` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and five focused maintenance tests passed.
- Kogwistar typing commit `afdd868` is pushed to its feature branch. The
  exact-SHA API query currently reports no workflow run (`runs=0`); local
  verification is Pyright-clean, Ruff-clean, and five short-id smoke tests
  passed.
- Kogwistar typing commit `4c25179` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and the resume-contract test passed.
- Kogwistar typing commit `688608c` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 12 focused server tests passed.
- Kogwistar typing commit `ba0fedc` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 2 targeted MCP tests passed.
- Kogwistar typing commit `3b2be8e` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 6 focused workflow/lineage tests passed.
- Kogwistar typing commit `b067011` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 3 workflow-design regression tests passed.
- Kogwistar typing commit `5fbfbfc` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 3 workflow-design regression tests passed.
- Kogwistar typing commit `fbaea0c` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, server Ruff-clean, and 2 MCP regression tests passed.
- Kogwistar typing commit `4d217a8` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 3 workflow-design regression tests passed.
- Kogwistar typing commit `b081654` is pushed to its feature branch. Exact-SHA
  workflow run `37946481813` completed successfully. Required CPython 3.12-3.14,
  PyPy 3.11, lint, Rust, SQLite invariants, and native-wheel smoke jobs passed;
  only the explicitly nonblocking PyPy 3.12 beta job failed. Local verification
  is full-source Pyright-clean, changed-file `E7,E9,F`-clean, and 78
  runtime/server tests passed with 2 skips.
- Kogwistar typing commit `9c173a0` is pushed after the successful `b081654`
  run. Exact-SHA workflow `37949128012` completed successfully. Required
  CPython 3.12-3.14, PyPy 3.11, lint, Rust, SQLite invariants, and native-wheel
  smoke jobs passed; only the explicitly nonblocking PyPy 3.12 beta job failed.
  Local full-source Pyright is clean across 262 files, and the maintenance-
  template regression passed.
- Kogwistar typing commit `414f8be` strengthens the durable metadata-store
  protocol with recursive JSON result/snapshot types, an event-delegate
  protocol, and matching Rust SQLite adapter signatures. Exact PR
  synchronization run `#498` is queued; local full-source Pyright is clean
  across 262 files, changed-file Ruff is clean, and the Rust registry
  differential test passed with 1 PostgreSQL fixture skipped.
- Kogwistar typing commit `b16f9ec` aligns the historical search facade, ACL
  forwarding, and shared read protocol around the explicit
  `similarity_threshold` parameter. Exact-SHA workflow run `37954185993` is
  currently loading on GitHub; it is not yet a green or failed result. Local
  full-source Pyright remains clean across 262 files.
- Kogwistar typing commit `78b7972` completes the auth service return contracts
  on top of the database boundary. Local source Pyright/Ruff are clean for the
  auth package and all 31 auth tests passed.
- Kogwistar typing commit `e9a2531` hardens CDC and visualization typing,
  removes mutable visualization defaults, and fixes runtime use of a
  type-checking-only engine import. Local strict Ruff/Pyright are clean and the
  relevant viewer/CDC tests passed 7 tests; optional Chroma tests still require
  the `chromadb` extra.
- Kogwistar typing commit `1d6ceb6` strengthens the existing mandatory ACL
  policy protocol with concrete decision/result types while preserving its
  runtime-checkable method set. The ACL protocol tests passed 3 tests; an
  attempted wider ACL run exposed only missing optional Chroma setup and an
  unrelated fixture mismatch.
- Kogwistar typing commit `5b327f5` annotates PostgreSQL UoW, collection,
  backend-constructor, and connection-context lifecycles. Local Pyright is
  clean and the async UoW, PostgreSQL metadata, and native session facade suite
  passed 13 tests; remaining Ruff findings are existing SQL/embedding boundary
  debt.
- KG Doc Parser typing commit `622b687` adds page-aware source collection,
  page, and unit protocols without widening the layered parser contract. Its
  exact-SHA GitHub result remains to be observed because the branch has no
  open PR; local full-source Pyright is clean, Ruff is clean for the touched
  modules, and the page-index/resolver suite passed 74 tests with 8 optional
  skips.
- KG Doc Parser typing commit `a724406` adds the reusable workflow engine
  capability protocol, reusing Core `ReadLike`/`WriteLike` contracts and
  preserving the concrete runtime factory boundary. Exact-SHA GitHub status
  remains unobserved because the branch has no open PR; local full-source
  Pyright is clean across 40 files and the focused page-index/resolver suite
  passed 74 tests with 8 optional skips.
- KG Doc Parser typing commit `affe290` publicly re-exports the new engine and
  persistence protocols from `workflow_ingest`; the export/import check,
  touched-file Pyright, and Ruff checks pass. No remote CI is claimed because
  the branch still has no open PR.
- LLM-Wiki typing commit `2a07057` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 44 focused multimodal tests passed with 3 skipped.
- LLM-Wiki typing commit `6868e5a` is pushed to its feature branch. The exact-SHA
  API query currently reports no workflow run (`runs=0`); local verification is
  Pyright-clean, Ruff-clean, and 21 embedding-service tests passed.
- LLM-Wiki typing commit `0ab6345` is pushed after the maintenance filter
  contract fix. Its exact-SHA API query currently reports no workflow run
  (`runs=0`); local verification is full-source Pyright-clean and 30
  maintenance orchestration tests passed.
- LLM-Wiki typing commit `72783fe` records the Core search protocol slice and
  closes two remaining concrete annotation findings in maintenance observation
  and graph-space query boundaries. No remote CI result is claimed because the
  branch has no open pull request; the targeted local tests reached 12 executed
  cases before the known shutdown hang.
- Kogwistar typing commit `9cb175f` annotates the Core `Span` construction,
  chunk-resolution, and validation contracts. Local Pyright remains clean,
  the model/flattening suite passed 38 tests, and the exact-SHA GitHub query
  reports `runs=0` because the branch has no attached workflow run.
- Kogwistar typing commit `8c110b2` annotates `Grounding` and graph-extraction
  evidence normalization/iteration contracts. Local Pyright remains clean,
  the same model/flattening suite passed 38 tests, and the exact-SHA GitHub
  query reports `runs=0`.
- Kogwistar typing commit `631403c` annotates storage-facing graph model
  metadata, tombstone normalization, and node/edge helper contracts. Local
  Pyright remains clean, the model/flattening suite passed 38 tests, and the
  exact-SHA GitHub query reports `runs=0`.
- Kogwistar typing commit `1efb288` narrows the `LevelAwareMixin` metadata
  validator from `Any` to a guarded raw payload. Local Pyright remains clean,
  the model/flattening suite passed 38 tests, and the exact-SHA GitHub query
  reports `runs=0`.
- Kogwistar typing commit `c513cd8` annotates flattened grounding evidence
  validators while preserving the non-empty evidence invariant. Local Pyright
  remains clean, the flattening/model suite passed 38 tests, and the exact-SHA
  GitHub query reports `runs=0`.
- Kogwistar typing commit `d18f510` annotates flattened graph reference
  validators for duplicate IDs and orphan references. Local Pyright remains
  clean, the flattened conversion suite passed 37 tests, and the exact-SHA
  GitHub query reports `runs=0`.
- Kogwistar typing commit `95f7170` annotates extraction context validators and
  conversion entry-point `insertion_method` contracts. Local Pyright remains
  clean, the flattened conversion suite passed 37 tests, and the exact-SHA
  GitHub query reports `runs=0`.
- Kogwistar typing commit `7c89a45` annotates `Document` constructors,
  source-map validation, chunking, and text access helpers. Local Pyright
  remains clean, direct constructor checks passed, the flattened conversion
  suite passed 37 tests, and the exact-SHA GitHub query reports `runs=0`.
- Kogwistar typing commit `a653cb8` annotates OCR page validation and split-page
  serialization return contracts. Local Pyright remains clean, split-document
  tests reached 13 passing cases before the broader rollback command exceeded
  its 30-second window, and the exact-SHA GitHub query reports `runs=0`.
- Kogwistar typing commit `965a620` annotates the legacy OCR document factory
  return contract. Local Pyright and Ruff are clean for `models.py`, the OCR
  constructor check passed, and the exact-SHA GitHub query reports `runs=0`.
- Kogwistar typing commit `a1f2dab` annotates conversation tail, embedding
  compatibility, and stable conversation identity helpers. Local Pyright and
  Ruff pass for the touched module, direct helper checks passed; the selected
  conversation test command exceeded its 30-second window before completion,
  and the exact-SHA GitHub query reports `runs=0`.
- Kogwistar typing commit `45098be` annotates conversation workflow entry-point
  inputs and previous-summary indexing. Local Pyright is clean, the module
  imports successfully, the selected conversation suite exceeded its 30-second
  window before completion, and the exact-SHA GitHub query reports `runs=0`.
- The `feat/stack-type-contracts` branch currently has an open Core PR and
  exact Core CI coverage. The parser and LLM-Wiki branches currently have no
  open PRs, so their latest commits cannot have pull-request CI results; this
  is a delivery-state gap, not evidence of a passing or failing downstream
  implementation.

## Measurement Commands

From each repository root:

```powershell
pyright <scope> 2>&1
python -m ruff check <scope> --select E4,E7,E9,F
python -m pytest <focused-tests> -q -p no:cacheprovider
```

For Kogwistar production-source typing, use the checked-in source-only
configuration so tests, demos, and generated cache code do not distort the
measurement:

```powershell
pyright --project pyright.source.json
```

For a numeric Pyright count in PowerShell:

```powershell
$out = pyright <scope> 2>&1 | Out-String
([regex]::Matches($out, ' - error:')).Count
```

Remote verification uses the exact pushed commit, not only a branch name:

```powershell
$sha = (git rev-parse HEAD).Trim()
$url = "https://api.github.com/repos/OWNER/REPO/actions/runs?head_sha=$sha"
$data = Invoke-RestMethod -Headers @{ Accept = "application/vnd.github+json" } -Uri $url
"sha=$sha runs=$($data.total_count)"
```

`runs=0` means that no remote workflow result is available yet; it is not a
passing result.

## Interpretation Rules

- A Pyright zero applies only to the named scope.
- A focused test pass proves behavior for those tests, not the entire suite.
- A Ruff pass does not prove protocol compatibility or runtime behavior.
- A GitHub branch push is not CI evidence until the exact SHA has a completed
  workflow result.
- Unrelated dirty files and generated artifacts must remain outside typing
  commits.
