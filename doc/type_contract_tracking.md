# Cross-Repository Type Contract Tracking

This ledger tracks the typing migration across the three coordinated
repositories.  A scoped zero is not a whole-repository completion claim.
Counts must be regenerated with the commands below after each structural
change.

## Branches

| Repository | Working branch | Latest typing commit |
| --- | --- | --- |
| `kogwistar` | `feat/stack-type-contracts` | `ec30eb7` |
| `kg-doc-parser` | `feat/stack-type-contracts` | `a72abd3` |
| `kogwistar-llm-wiki` | `feat/stack-type-contracts` | `6f8ca21` |

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
| Kogwistar | `runtime/telemetry.py`, `runtime/models.py`, `runtime/__init__.py` | 0 errors, 0 warnings | passed | telemetry slice: 22 passed; one Chroma-backed integration case unavailable locally |
| Kogwistar | `engine_core/embedding_profile.py` JSON/profile boundary | 0 errors, 0 warnings | selected check has legacy resolver findings outside this slice | 16 passed, 36 skipped |
| Kogwistar | `engine_core/in_memory_backend.py` sync/async projection adapters | 0 errors, 0 warnings | passed | 92 passed, 24 skipped; 6 Chroma cases unavailable without `chromadb` |
| Kogwistar | `engine_core/engine_sqlite.py` SQLite and projection JSON boundaries | 0 errors, 0 warnings | passed | 17 passed, 2 skipped; PostgreSQL fixture unavailable |
| Kogwistar | `engine_core/subsystems/read.py` graph result and adapter boundaries | 0 errors, 0 warnings | passed | 38 passed, 2 skipped, 6 deselected |
| Kogwistar | `engine_core/subsystems/write.py`, `typing_interfaces.py` write and backend protocol boundaries | 0 errors, 0 warnings | passed | 35 passed, 7 skipped, 9 deselected; Chroma unavailable locally |
| Kogwistar | `engine_core/postgres_backend.py` PostgreSQL event, async-result, and vector-buffer boundaries | 0 errors, 0 warnings | passed | 4 focused tests passed, 1 optional `pgvector` test unavailable locally, 1 PostgreSQL fixture skipped |
| Kogwistar | `utils/file_loader.py` path-pattern, loader, callback, and iterator contracts | 0 errors, 0 warnings | passed | direct loader smoke passed; no dedicated focused test module found |
| Kogwistar | `wisdom/resolvers.py` typed dream workflow context, payload, dependency, and callback boundaries | 0 errors, 0 warnings | passed | 4 dream workflow tests passed |
| Kogwistar | `runtime/runtime.py` trace payload, backend lookup, context-manager, cache-path, and join-helper contracts | 0 errors, 0 warnings | legacy E402 only | 86 passed, 2 skipped (optional Chroma/PostgreSQL fixtures) |
| Kogwistar | `server/chat_api.py` FastAPI response and SSE iterator contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 9 passed, 2 warnings |
| Kogwistar | `strategies/adjudicators.py` task, payload, cache, and batch contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | fake-backend batch test passed; optional Chroma cases unavailable locally |
| Kogwistar | `engine_core/utils/aliasing.py` graph alias and de-alias contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 8 passed; 2 optional Chroma cases unavailable locally |
| Kogwistar | `runtime/async_runtime.py` async workflow invocation, edge, persistence, and callback contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F` except pre-existing E402 in the monolithic module) | 86 passed, 2 skipped; optional Chroma/PostgreSQL fixtures unavailable locally |
| Kogwistar | `runtime/design.py` workflow graph reader, resolver, and validation contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 1 available workflow-design test passed; 1 PostgreSQL case skipped and 2 Chroma cases unavailable locally |
| Kogwistar | `engine_core/in_memory_backend.py` engine, model, transaction, and lock contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 54 passed, 6 skipped; 4 Chroma cases unavailable locally |
| Kogwistar | `server/mcp_tools.py` ASGI middleware, graph-tool, adjudication, and public MCP tool contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 22 MCP/auth tests passed, 1 optional test skipped; golden schema parity passed |
| Kogwistar | `runtime/rust_runtime_authority.py` sync/async Rust scheduler, HTTP, and result contracts | 0 errors, 0 warnings | passed (`ANN,E4,E7,E9,F`) | 8 authority tests passed, 2 optional Rust-server tests skipped; 5 legacy fixture-construction cases remain separately failing |
| Kogwistar | `utils/log.py` logger hierarchy, SQLite handler, and per-engine filter contracts | 0 errors, 0 warnings | passed (`ANN,E7,E9,F`); legacy E402 layout warnings remain | direct SQLite logging smoke passed; explicit connection cleanup verified on Windows |
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
| Kogwistar | `kogwistar` full source scan | 0 errors, 0 warnings across 276 files after `1563ec9` | configured Ruff passes | exact-SHA Actions run `38005794530` is visible on GitHub; terminal result still requires completion; optional backend and incomplete-fixture tests remain separately scoped |
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

- Complete strict `ANN` annotation coverage in Kogwistar Core (`61`
  findings), prioritizing public protocol
  boundaries and callback surfaces over mechanical private helpers.
- Run the full relevant test and CI gates for each pushed slice; Core SHA
  `ec30eb7` is pushed; exact-SHA workflow `38010907498` is in progress and must
  reach a terminal result before it is counted as remotely verified.
  Parser SHA `a72abd3` has local verification, but no exact-SHA
  workflow has appeared in the API response.

## Quantified Progress

Current production-source annotation snapshot (`ruff check kogwistar --select ANN`):

| Repository | Pyright | Strict ANN findings | Interpretation |
| --- | ---: | ---: | --- |
| Kogwistar | 0 errors, 0 warnings across 276 files | 61 | remaining Core annotation/protocol migration |
| KG Doc Parser | 0 errors, 0 warnings across 40 files | 0 | strict-ANN clean; runtime verification remains |
| LLM-Wiki | 0 errors, 0 warnings across 181 files | 0 in `src/` | production source is strict-ANN clean |

These counts are not a percentage. They are the denominator needed for a
reproducible percentage after the migration policy defines which generated,
benchmark, and test files are in scope. Full CI status is tracked separately;
local Pyright and Ruff success cannot substitute for remote CI.

The current strict production-source backlog is `61` findings: Kogwistar
`61`, KG Doc Parser `0`, and LLM-Wiki `0` in `src/`. The previous
recorded total was `1,669`; the reductions came from completed Core and parser
contract slices, not from excluding files.

That is `1608 / 1,669` findings resolved, or approximately `96.3%`; `3.7%`
remains under this strict-annotation metric. This is a migration-health
measure, not a feature-completion percentage.

For the Kogwistar full-source Pyright backlog, the first reproducible
source-only baseline was 194 errors. The last reproducible scan before
`1ffdfd4` reported 180 errors, concentrated in the runtime JSON boundaries.
The full source-only scan after `ec30eb7` reports 0 errors and 0 warnings.
This is a backlog metric only; it does not claim that runtime behavior is
fully verified, and it does not count Ruff findings or unverified CI as
completed work. A slice is counted only after its full-scope scan is
regenerated.

- Current measured backlog: KG Doc Parser `workflow_ingest` and the full
  `kg_doc_parser` source scan have `0` Pyright errors and `0` warnings and the
  parser-wide strict ANN scan has `0` findings; Kogwistar full source-only scan
  after `ec30eb7` measures `0` errors and `0` warnings, with `61` strict ANN
  findings remaining. This is a typing
  measurement,
  not a passing runtime gate; optional backend fixtures and full CI remain
  separately unverified.
The latest runtime, maintenance, ontology, PostgreSQL, and chat API slices are clean; the remaining
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

## Current Exact-SHA Gate

- Kogwistar `ec30eb7` is pushed to `feat/stack-type-contracts`; exact-SHA
  GitHub Actions run `38010907498` is visible on the public Actions page and
  remains in progress. No remote success is claimed until it reaches a terminal
  result.
- No remote success is
  claimed until its Python matrix reaches a terminal result.
- Fresh local Pyright is clean across Core (`276` files), KG Doc Parser
  (`40` files), and LLM-Wiki (`181` files).

## Remote Verification Status

- Kogwistar typing commit `97906ec` is pushed to
  `feat/stack-type-contracts`; the exact-SHA API query currently reports no
  workflow run (`runs=0`). Local verification is Pyright-clean for the
  changed subsystem, strict `ANN`-clean for the changed files, and the
  applicable fake-backend regressions passed. Chroma-backed cases were not
  runnable locally because `chromadb` is not installed.

- Kogwistar typing commit `9540eb5` completes the
  `engine_core/subsystems/persist.py` annotation boundary. Local Pyright and
  strict `ANN` checks are clean for the changed file; focused persistence tests
  reached backend setup and were stopped after stalling locally, so no runtime
  pass is claimed. Exact-SHA GitHub workflow `37981137242` is pending; no
  remote success is claimed yet.

- Kogwistar typing commit `83b63f6` completes the
  `engine_core/subsystems/rollback.py` annotation boundary. Local Pyright and
  strict `ANN` checks are clean for the changed file; focused rollback tests
  stalled at backend setup without an assertion failure, so no runtime pass is
  claimed. The exact-SHA workflow result is pending registration.

- Kogwistar typing commit `54b6261` strengthens the ACL read adapter and
  removes dynamic forwarding from its core read methods. Local Pyright and
  strict `ANN` checks are clean for the changed file; ACL protocol/graph tests
  passed through the available cases before optional backend setup stalled.
  Exact-SHA workflow `37981695060` is pending; no remote success is claimed.

- Kogwistar typing commit `33ac29a` formalizes the Joblib/DiskCache/no-cache
  provider protocol and typed cache persistence boundary. Local Pyright and
  strict `ANN` checks are clean; the DiskCache positional-ignore regression
  passed. The cross-backend cache test cannot run locally because `chromadb`
  is not installed. Exact-SHA workflow `37981879828` is pending.

- Kogwistar commit `c6bc55d` removes four unused imports reported by the full
  repository Ruff gate. Local `python -m ruff check .` passes. Exact-SHA
  workflow `37987976204` is queued; no remote test result is claimed yet.

- Kogwistar typing commit `e0451eb` strengthens the authentication middleware
  ASGI, claims, context-token, and authorization helper contracts. Local
  Pyright and strict `ANN` checks are clean; focused auth/runtime scope tests
  passed 9 tests. The prior exact-SHA run `37987976204` is still in progress
  with PyPy 3.11 failed and SQLite invariants passed; no green remote result is
  claimed for this newer commit yet.

- Kogwistar typing commit `908fdcd` completes the public graph-query facade
  contracts for traversal flags, backend filters, and semantic expansion
  results. Local Pyright and strict `ANN` checks are clean, and the focused
  graph-query suite passed 6 tests. The latest Core CI run remains in progress.

- Kogwistar typing commit `3020dbc` completes the file-loader path-pattern,
  callback, and iterator contracts. Local Pyright and strict `ANN` checks are
  clean, and a direct loader smoke passed. Exact-SHA workflow `37988563721`
  is pending; no remote success is claimed yet.

- Kogwistar typing commit `7179eb7` completes the runtime context, trace
  payload, backend lookup, context-manager, cache-path, and join-helper
  contracts. Local Pyright and strict `ANN` checks are clean; the focused
  runtime suite passed 86 tests with 2 optional backend skips. Exact-SHA
  workflow `37989689911` is pending; no remote success is claimed yet.

- Kogwistar typing commit `c4a4f35` completes the `server/chat_api.py`
  FastAPI response and SSE iterator contracts. Local Pyright and strict `ANN`
  checks are clean, and the focused chat API smoke suite passed 9 tests with 2
  warnings. Exact-SHA workflow `37990138209` is pending; no remote success is
  claimed yet.

- Kogwistar typing commit `194aac6` completes the adjudicator task, payload,
  cache, and batch contracts. Local Pyright and strict `ANN` checks are clean;
  the fake-backend regression passed and optional Chroma cases were unavailable.
  Exact-SHA workflow `37990768861` is in progress; no remote success is claimed.

- Kogwistar typing commit `0ff0423` completes the graph aliasing and de-aliasing
  contracts. Local Pyright and strict `ANN` checks are clean; 8 available tests
  passed and 2 Chroma cases were unavailable. Exact-SHA workflow `37991097509`
  is pending; no remote success is claimed.

- Kogwistar typing commit `47c05fb` completes the async workflow invocation,
  edge-selection, persistence-compatibility, and callback contracts. Local
  Pyright and strict `ANN` checks are clean; 86 focused async-runtime tests
  passed with 2 optional backend skips. Exact-SHA workflow `37991847824` is
  pending; no remote success is claimed.

- Kogwistar typing commit `74873ed` completes the workflow design graph-reader,
  resolver, and validation contracts. Local Pyright and strict `ANN` checks are
  clean; the available workflow-design case passed, with Chroma unavailable
  locally. Exact-SHA workflow `37992458923` is queued; no remote success is
  claimed.

- Kogwistar typing commit `12dbe64` completes the in-memory backend engine,
  graph-model, transaction, and lock contracts. Local Pyright and strict `ANN`
  checks are clean; 54 backend/two-stage/meta tests passed, with optional Chroma
  cases unavailable locally. Exact-SHA workflow `37993011702` is in progress;
  no remote success is claimed.

- Kogwistar typing commit `e7af064` completes the MCP tool ASGI middleware,
  graph-tool, adjudication, and public return contracts. Local Pyright and
  strict `ANN` checks are clean; 22 MCP/auth tests passed with 1 optional skip,
  and the golden MCP schema parity test passed. Exact-SHA workflow
  `37993616509` is pending; no remote success is claimed.

- Kogwistar typing commit `cc8b043` completes the sync/async Rust runtime
  authority HTTP, scheduler, and result contracts. Local Pyright and strict
  `ANN` checks are clean; 8 authority tests passed and 2 optional Rust-server
  tests were skipped. Exact-SHA workflow `37993949288` is pending; no remote
  success is claimed.

- Kogwistar typing commit `506f0b9` completes the logger hierarchy, SQLite
  handler, and per-engine filter contracts. Local Pyright and strict `ANN`
  checks are clean; the direct SQLite logging smoke passed with explicit
  Windows connection cleanup. Exact-SHA workflow `37994249674` is pending; no
  remote success is claimed.

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
- Kogwistar typing commits `325b773`, `1cdba3f`, `1814121`, and `2bfa194` annotate
  conversation service, workflow materialization, and native-store JSON
  boundaries. Local focused checks pass; the latest exact-SHA query reports
  no workflow run, so no remote success is claimed yet.
- Kogwistar typing commit `3e71e00` preserves decorated SQLite method
  signatures with `ParamSpec` and typed sync/async wrappers. Local Pyright and
  strict Ruff are clean, and all 21 SQLite context invariant tests passed.
- Kogwistar typing commit `0eefd47` narrows optional LangChain callback hooks
  and fallback stubs. Local Pyright and strict Ruff are clean, and 8 optional
  dependency-boundary tests passed.
- Kogwistar typing commit `5f836ff` annotates the SQLite connection context
  override and legacy entity-event iterator. Local Pyright and strict Ruff are
  clean, and 7 Rust/Python SQLite differential tests passed. Exact-SHA remote
  CI has not been observed for these latest commits.
- Kogwistar typing commit `5c23229` annotates PostgreSQL transaction helper
  lifecycles and event iteration. Local Pyright and strict Ruff are clean, and
  6 PostgreSQL metadata/queue-safety tests passed. Exact-SHA CI run
  `37969410109` is pending; it is not yet a success.
- Kogwistar typing commit `9d74dd1` narrows the PostgreSQL async adapter,
  coroutine ferry, JSON decoding, and buffered-result boundaries. Local
  Pyright and strict Ruff are clean, and the same 6 metadata/queue-safety
  tests passed. The exact-SHA API currently reports no workflow run.
- Kogwistar typing commit `9aaf126` narrows Rust SQLite operation inputs to
  `JsonValue` and makes the selector return type explicit without weakening
  the native boundary. Local Pyright and strict Ruff pass; 15 Rust authority
  and differential tests passed. The exact-SHA API currently reports no
  workflow run.
- KG Doc Parser typing commit `9458981` annotates the version-chain database
  and file metadata helpers. Local Pyright and the semantic parser suite pass;
  exact-SHA CI currently reports no workflow run.
- KG Doc Parser typing commit `9211bfe` annotates CLI result boundaries, OCR
  callback inputs, demo context-manager cleanup, and layerwise review
  callbacks. Local focused Pyright is clean, strict ANN is clean for the
  touched modules, and the semantic parser suite passed 7 tests with 7
  environment-gated skips. The exact-SHA API query currently reports no
  workflow run (`runs=0`).
- KG Doc Parser typing commit `a3032d4` annotates semantic-tree traversal,
  parser retry boundaries, and explicit model conversion at the low-level/high-
  level parser seam. Local Pyright is clean, the semantic parser suite passed
  7 tests with 7 environment-gated skips, and the parser-wide strict ANN count
  is now 45. The exact-SHA API currently reports no workflow run (`runs=0`).
- KG Doc Parser typing commit `fdd79e7` annotates frontend child preparation,
  iterative review-loop inputs/outputs, and deterministic pointer correction
  boundaries. Local Pyright is clean, strict ANN findings in the parser are
  down to 32, and the semantic parser suite passed 7 tests with 7
  environment-gated skips. The exact-SHA API currently reports no workflow
  run (`runs=0`).
- KG Doc Parser typing commit `ab2409d` annotates semantic CUD proposal
  models and nested traversal helpers. Local Pyright is clean, the focused
  semantic parser suite passed 25 tests with 7 environment-gated skips, and
  the parser-wide strict ANN count is now 21. The exact-SHA API currently
  reports no workflow run (`runs=0`).
- KG Doc Parser typing commit `a72abd3` closes the remaining semantic helper
  annotations and narrows the parser client JSON counter boundary after the
  Core JSON types became stricter. Full parser Pyright remains clean across
  40 files; the focused semantic/resolver suite passed 25 tests with 7
  environment-gated skips. The exact-SHA API currently reports no workflow
  run (`runs=0`).
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
