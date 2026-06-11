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
