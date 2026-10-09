# Cross-Repository Type Contract Tracking

This ledger tracks the typing migration across the three coordinated
repositories.  A scoped zero is not a whole-repository completion claim.
Counts must be regenerated with the commands below after each structural
change.

## Branches

| Repository | Working branch | Latest typing commit |
| --- | --- | --- |
| `kogwistar` | `feat/stack-type-contracts` | `aba1a01` |
| `kg-doc-parser` | `feat/stack-type-contracts` | `af2b62a` |
| `kogwistar-llm-wiki` | `feat/stack-type-contracts` | `3fdeb0e` |

## Verified Scopes

| Repository | Scope | Pyright result | Ruff result | Focused tests |
| --- | --- | --- | --- | --- |
| Kogwistar | `agent/read_tools.py` | 0 errors | passed | 44 passed |
| Kogwistar | `agent/bindings.py` | 0 errors | passed | ACL/goal-agent suite passed |
| Kogwistar | `agent/control.py`, `delegation.py`, `limits.py` | 0 errors | passed | 30 agent tests passed |
| Kogwistar | `runtime/budget.py` | 0 errors | passed | 16 passed |
| Kogwistar | `runtime/rust_worker.py` protocol boundary | 50 errors, 0 warnings | passed | 21 passed |
| Kogwistar | `kogwistar` full source scan | 2,250 errors, 24 warnings across 275 files | not yet run for full scope | baseline measurement |
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
  Kogwistar full source has `2,250` errors and `24` warnings.
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
