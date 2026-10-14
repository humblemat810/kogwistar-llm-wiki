# Parser Source-Pointer Boundary Fix Checklist

This checklist tracks the LLM-Wiki ingestion boundary that consumes parser
semantic trees. An item is checked only when code and a reusable test or CI
evidence support it.

## Regression And Implementation

- [x] Preserve the original crosslink integration regression fixture,
  including its legacy exclusive-end pointer values.
- [x] Preserve the request-text source-map fallback when a parser result does
  not carry its own map.
- [x] Rehydrate every `total_content_pointers` value from the authoritative
  source map before strict graph export.
- [x] Rehydrate every `aggregate_content_pointers` value at the same boundary.
- [x] Rehydrate nested semantic children without mutating the parser result.
- [x] Rehydrate a valid offset range even when the provider transcription
  differs, with a dedicated application-boundary regression test.
- [x] Reject missing, ambiguous, or unrepairable pointers instead of silently
  persisting them.
- [x] Respect an explicitly supplied source map, including an explicit empty
  map, instead of silently substituting another map.
- [x] Keep strict parser export validation enabled after repair.

## Verification

- [x] Ruff passes for the changed production module.
- [x] `git diff --check` passes.
- [x] Focused regression passes on CPython 3.13 with the vendored Kogwistar
  and parser import paths: `1 passed in 59.86s`.
- [x] Ingestion unit tests plus the crosslink integration suite pass on the
  same environment: `15 passed in 204.75s (0:03:24)`.
- [ ] Required GitHub CI passes on the fresh corrective PR.
- [ ] Corrective PR is merged into `main`.

## Scope Guardrails

- [x] Do not weaken source-pointer validation.
- [x] Do not change the regression test to fit the exporter failure.
- [x] Do not stage unrelated workspace artifacts or `AGENTS.md` changes.
- [x] Do not treat the optional PyPy 3.12 beta lane as required CI.
