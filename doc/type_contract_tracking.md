# Cross-Repository Type Contract Tracking

This ledger tracks the typing migration across the three coordinated
repositories.  A scoped zero is not a whole-repository completion claim.
Counts must be regenerated with the commands below after each structural
change.

## Branches

| Repository | Working branch | Latest typing commit |
| --- | --- | --- |
| `kogwistar` | `feat/stack-type-contracts` | `63c1ce1` |
| `kg-doc-parser` | `feat/stack-type-contracts` | `be46033` |
| `kogwistar-llm-wiki` | `feat/stack-type-contracts` | `f6b4dc1` |

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
| Kogwistar | `engine_core/in_memory_meta.py` metadata and projection JSON boundaries | 0 errors, 0 warnings | passed | 22 passed, 2 warnings |
| Kogwistar | `typing_interfaces.py` plus engine subsystem protocol surface | 0 errors in protocol file; engine reduced to 44 | passed | fake backend smoke passed; optional Chroma unavailable locally |
| Kogwistar | ACL read/write protocol forwarding and backend capability attributes | engine reduced to 34 errors | passed | 42 ACL tests passed; 1 unrelated fixture-helper failure |
| Kogwistar | `engine_core/engine.py` and optional projection capability protocol | 0 errors, 0 warnings | legacy E402 in existing monolithic engine import layout | 70 passed, 8 skipped; Chroma unavailable locally; 1 unrelated ACL fixture-helper failure |
| Kogwistar | `conversation/models.py`, `conversation_context.py`, `memory_retriever.py` | 0 errors, 0 warnings | passed | 9 passed, 2 warnings |
| Kogwistar | `conversation/resolvers.py` state boundary and retrieval fallback | 44 errors, 0 warnings | passed | 18 passed, 8 warnings |
| Kogwistar | `conversation/conversation_orchestrator.py`, `conversation/tool_runner.py`, `engine_core/types.py` | 0 errors, 0 warnings | passed | 18 passed, 8 warnings |
| Kogwistar | `conversation/agentic_answering.py` | 0 errors, 0 warnings | passed | 19 passed; Chroma/real-LLM cases unavailable locally |
| Kogwistar | `conversation/service.py` | 0 errors, 0 warnings | passed | fake cancellation/causality paths passed; Chroma unavailable locally |
| Kogwistar | `messaging/service.py`, lane-message store protocol and projection records | 0 errors, 0 warnings across 3 messaging files | passed | 27 lane-message, visibility, projection-rebuild, and metastore-contract tests passed |
| Kogwistar | `runtime/checkpointed_projection.py` | 0 errors, 0 warnings | passed | 6 checkpoint, CAS, failure-preservation, and bounded-tail tests passed |
| Kogwistar | `runtime/telemetry.py`, `runtime/models.py`, `runtime/__init__.py` | 0 errors, 0 warnings | passed | 12 budget/projection regression tests passed |
| Kogwistar | `engine_core/embedding_profile.py` JSON/profile boundary | 0 errors, 0 warnings | selected check has legacy resolver findings outside this slice | 16 passed, 36 skipped |
| Kogwistar | `engine_core/in_memory_backend.py` sync/async projection adapters | 0 errors, 0 warnings | passed | 92 passed, 24 skipped; 6 Chroma cases unavailable without `chromadb` |
| Kogwistar | `engine_core/engine_sqlite.py` SQLite and projection JSON boundaries | 0 errors, 0 warnings | passed | 17 passed, 2 skipped; PostgreSQL fixture unavailable |
| Kogwistar | `engine_core/subsystems/read.py` graph result and adapter boundaries | 0 errors, 0 warnings | passed | 38 passed, 2 skipped, 6 deselected |
| Kogwistar | `engine_core/subsystems/write.py`, `typing_interfaces.py` write and backend protocol boundaries | 0 errors, 0 warnings | passed | 35 passed, 7 skipped, 9 deselected; Chroma unavailable locally |
| Kogwistar | `engine_core/postgres_backend.py` PostgreSQL event, async-result, and vector-buffer boundaries | 41 errors, 0 warnings | passed | 10 passed, 2 skipped; PostgreSQL fixtures unavailable locally |
| Kogwistar | `engine_core/engine_postgres_meta.py` async/sync engine mode boundaries | 0 errors, 0 warnings | passed | 19 passed, 1 skipped; PostgreSQL fixture unavailable locally |
| Kogwistar | `runtime/perf_profile.py` benchmark protocol and JSON-report boundaries | 0 errors, 0 warnings | passed | 15 tests collected, all environment-gated/skipped locally |
| Kogwistar | `server/chat_service_run_execution.py`, `chat_service_shared.py`, `run_registry.py` execution and telemetry protocols | 0 errors, 0 warnings | passed | 14 passed, 7 skipped; 9 Chroma-dependent setup errors locally |
| Kogwistar | `conversation` full scoped package | 0 errors, 0 warnings across 19 files | four pre-existing Ruff findings remain in `agentic_answering_design.py` and `conversation_context.py` | 14 focused workflow/agentic tests passed; Chroma/real-LLM cases remain environment-gated |
| Kogwistar | `conversation/policy.py` policy hooks and backend JSON narrowing | 0 errors, 0 warnings | passed (`E4,E7,E9,F`) | 4 passed, 17 skipped |
| Kogwistar | `engine_core/subsystems/extract.py` and `llm_tasks/default_provider.py` | 0 errors, 0 warnings | passed | provider/structured-output tests passed; Chroma-dependent extraction tests unavailable locally |
| Kogwistar | `server/bootstrap.py` | 0 errors, 0 warnings | passed | 4 bootstrap tests passed |
| Kogwistar | `server/auth` | 0 errors, 0 warnings | passed | 39 auth/integration tests passed |
| Kogwistar | `server/chat_service.py`, `chat_service_shared.py`, `chat_service_conversation_queries.py`, `chat_service_run_execution.py` | 0 errors, 0 warnings | passed | async event suite skipped because Chroma/Postgres fixtures are unavailable locally |
| Kogwistar | `conversation/policy.py`, `server/resources.py`, `server/chat_service_run_inspection.py`, `shortids.py`, `utils/log.py` | 0 errors, 0 warnings | passed for focused files; legacy E402 remains in `utils/log.py` | focused compatibility checks passed; backend-dependent tests remain environment-gated |
| Kogwistar | `kogwistar` full source scan | 209 errors, 3 warnings across the current source | not yet run for full scope | last regenerated full-scope measurement after `5d6a908`; the search-index slice is clean in isolation, but the full count has not yet been regenerated; remote verification for `63c1ce1` is unavailable |
| KG Doc Parser | `workflow_ingest/clients.py`, `demo_harness.py` | 0 errors | passed | 6 passed, 2 skipped |
| KG Doc Parser | `workflow_ingest/handlers.py` | 0 errors | passed | resolver/demo suites passed |
| KG Doc Parser | `workflow_ingest/serialization.py` | 0 errors | passed | serialization callers covered |
| KG Doc Parser | `workflow_ingest/service.py` | 0 errors | passed | 18 resolver tests passed |
| KG Doc Parser | `workflow_ingest` (full scoped package) | 0 errors | passed for touched modules | focused run reached all selected cases but was stopped during shutdown; not counted as a pass |
| KG Doc Parser | `kg_doc_parser` full source scan | 0 errors, 0 warnings across 40 files | 182 legacy Ruff findings remain | full Pyright scan completed; runtime/integration coverage remains separate |
| LLM-Wiki | ingestion/parsing/workbench targeted scope | 0 errors | passed | 14 passed, 1 deselected |
| LLM-Wiki | `src/kogwistar_llm_wiki` full scope | 0 errors, 0 warnings across 174 files | targeted groups passed; full runtime suite pending | Pyright clean |
| LLM-Wiki | diagnostics, remote embedding, trace sink, and memory boundaries | 0 errors, 0 warnings across full `src` scan | passed (`E4,E7,E9,F`) | 28 passed |

## Remaining Work

## Quantified Progress

For the Kogwistar full-source Pyright backlog, the fixed baseline is 277
errors. The current scan reports 209 errors, so the measured diagnostic
reduction is 68/277 = 24.5%. This is a backlog metric only; it does not claim
that 75.5% of runtime behavior is broken, and it does not count warnings,
Ruff findings, or unverified CI as completed work. A slice is counted only
after its full-scope scan is regenerated.

- Current measured backlog: KG Doc Parser `workflow_ingest` and the full
  `kg_doc_parser` source scan have `0` Pyright errors and `0` warnings;
  the parser-wide Ruff scan currently has `182` legacy findings;
  Kogwistar full source currently measures `209` errors and `3` warnings across
  the current source after the latest protocol slices; this is a measurement, not a
  passing gate. The largest remaining groups are `postgres_backend.py`, demo
  modules, and selected server/runtime boundaries.
  The latest runtime/ontology slices are clean; the PostgreSQL backend remains
  an active follow-up slice, and the full-source backlog is still open.
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
- [ ] Reduce the Kogwistar full-source backlog by package, starting with core
      runtime/engine protocol boundaries and then the remaining subsystems.
- [x] Audit all LLM-Wiki packages beyond the current targeted scope.
- [x] Remove the measured LLM-Wiki backlog of 30 errors, grouped by policy,
      transport/protocol, JSON boundaries, and model contracts.
- [ ] Run full local CI for each repository with the documented dependency
      paths and `-p no:cacheprovider` where appropriate.
- [ ] Verify PyPy 3.11 and CPython 3.12-3.14 compatibility after type changes.
- [ ] Observe GitHub Actions for each pushed exact SHA before treating a slice
      as remotely verified.
- [ ] Reconcile this ledger after every commit or rebase.

## Remote Verification Status

- `kogwistar-llm-wiki` PR #42 was merged at `b5149d5`; its last visible CI
  run tested `b8a7e55` and was green, but the merged PR retained one separate
  failed status check. Commit `f6b4dc1` was pushed afterward to the already
  merged head branch, so it has no new pull-request synchronization run.
- No GitHub Actions run currently exists for exact Kogwistar SHA `63c1ce1`.
  The earlier run `37904203244` for `b9001dd` was cancelled/superseded; no
  remote green result is claimed for the current branch.

## Measurement Commands

From each repository root:

```powershell
pyright <scope> 2>&1
python -m ruff check <scope> --select E4,E7,E9,F
python -m pytest <focused-tests> -q -p no:cacheprovider
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
