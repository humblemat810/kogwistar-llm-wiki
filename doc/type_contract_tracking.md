# Cross-Repository Type Contract Tracking

This ledger tracks the typing migration across the three coordinated
repositories.  A scoped zero is not a whole-repository completion claim.
Counts must be regenerated with the commands below after each structural
change.

## Branches

| Repository | Working branch | Latest typing commit |
| --- | --- | --- |
| `kogwistar` | `feat/stack-type-contracts` | `64ab58e` |
| `kg-doc-parser` | `feat/stack-type-contracts` | `af2b62a` |
| `kogwistar-llm-wiki` | `feat/stack-type-contracts` | `f635e36` |

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
| Kogwistar | `conversation` full scoped package | 69 errors, 0 warnings across 19 files after agentic/service slices | not yet rerun for full scope | Chroma/real-LLM tests remain environment-gated |
| Kogwistar | `kogwistar` full source scan | 1,894 errors, 24 warnings across the scanned package | not yet run for full scope | post-engine-contract measurement |
| KG Doc Parser | `workflow_ingest/clients.py`, `demo_harness.py` | 0 errors | passed | 6 passed, 2 skipped |
| KG Doc Parser | `workflow_ingest/handlers.py` | 0 errors | passed | resolver/demo suites passed |
| KG Doc Parser | `workflow_ingest/serialization.py` | 0 errors | passed | serialization callers covered |
| KG Doc Parser | `workflow_ingest/service.py` | 0 errors | passed | 18 resolver tests passed |
| KG Doc Parser | `workflow_ingest` (full scoped package) | 0 errors | passed for touched modules | focused run reached all selected cases but was stopped during shutdown; not counted as a pass |
| KG Doc Parser | `kg_doc_parser` full source scan | 11 errors, 1 warning across 40 files | legacy module Ruff backlog remains | checked-out-core semantic test path passed; full parameterized run not completed |
| LLM-Wiki | ingestion/parsing/workbench targeted scope | 0 errors | passed | 14 passed, 1 deselected |
| LLM-Wiki | `src/kogwistar_llm_wiki` full scope | 0 errors, 0 warnings across 174 files | targeted groups passed; full runtime suite pending | Pyright clean |

## Remaining Work

- Current measured backlog: KG Doc Parser `workflow_ingest` has `0`
  Pyright errors; the parser-wide backlog is `11` errors and `1` warning;
  Kogwistar full source has `1,894` errors and `24` warnings. The conversation
  resolver, cache-wrapper, orchestration, agentic-answering, and service slices
  are each measured at `0` errors and `0` warnings; the full conversation
  package is now `69` errors, down from `300` before the conversation typing
  work began.
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
