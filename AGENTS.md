# Agent Notes

## Scope And Ownership

- Put reusable parser design, parser ADRs, parser checklists, and parser
  implementation under `kg-doc-parser/`.
- Put reusable runtime, graph, workflow, budget, and substrate primitives under
  `kogwistar/`.
- Put product orchestration, VS Code launch configs, long-run harnesses,
  promotion policy, and app-level reports under this repo's root `src/`,
  `.vscode/`, `tests/`, and `doc/`.
- Root `doc/` is appropriate for `kogwistar-llm-wiki` product docs and
  cross-repo responsibility notes. Parser-owned docs should live under
  `kg-doc-parser/doc/`.
- Plans should always first think if `kogwistar` primitives can be reused
  before adding app-local runtime behavior.

## Testing

### Fixed-Batch TDD For Existing Goals

- For each existing goal item, first enumerate its complete, bounded set of
  required executable tests and record the exact selectors. Run or record the
  baseline before implementation; do not inflate checklist denominators.
- Add the whole test batch first. Once implementation begins, do not add tests
  merely to defer closure. Add a test later only for a reproduced regression or
  an explicitly approved contract change, and document why.
- Implement against the frozen batch, then run in order: focused unit tests,
  followed by the smallest relevant integration batch. Fix failures without
  broadening scope.
- When every frozen applicable test passes, mark that existing goal item PASS
  immediately and record selector/backend/evidence. If a required case is
  missing, say so explicitly; never imply the batch is complete.
- Keep existing goal/checklist IDs and denominators authoritative. Do not
  reopen completed items without concrete regression, invalid prior evidence,
  or approved contract change.
- For each slice, report tests and results, compatibility evidence, completion
  state, remaining blockers, and coarse/fine checklist percentages. Commit
  tested slices; batch pushes rather than pushing every commit, and inspect CI
  after each push.

- Before treating a pytest timeout-after-success as an application failure,
  check the pytest cache path. On this Windows workspace, unwritable cache
  directories have repeatedly caused commands to reach `100% passed` and then
  hang at shutdown.
- Prefer the configured repo-local cache. For vendored `kogwistar`, this is
  `cache_dir = .pytest-local-cache` in `kogwistar/pytest.ini`.
- Do not use `-o cache_dir=C:\tmp\...` unless a write probe proves this process
  can create directories there.
- For a quick signal when cache behavior is suspect, run pytest with
  `-p no:cacheprovider`.
- A pytest run that reaches `100% passed` and then hangs is usually a cache
  shutdown problem, not a product regression. Confirm the cache path before
  chasing app logic.
- See `doc/testing_guide.md` before inventing a new pytest cache workaround.

## Typing And Hinting

- Prefer concrete type hints over placeholder `Any` when the runtime contract is
  already known.
- Add explicit return annotations to production functions and methods when they
  have a stable return shape.
- For structured LLM outputs, queue jobs, provider builders, context-manager
  factories, and callback surfaces, prefer domain models, `Literal`, unions,
  `Protocol`, `TypedDict`, or named type aliases before falling back to `Any`.
- Keep `Any` only at true external boundaries such as raw third-party payloads,
  opaque SDK objects, or short-lived compatibility shims that cannot yet be
  narrowed safely.

# Source management Policy

On every push, you must watch github CI results for errors instead of push and forget

# multi version support

It need to support pypy3.11 and cpython 3.12 to 3.14, so for maximum compatibility, some ruff suggestion has to be dropped
