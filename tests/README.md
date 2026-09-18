# Test Organization

The root test suite follows the application boundary described in
[`doc/repository_structure.md`](../doc/repository_structure.md).

- `unit/` contains deterministic provider-free contracts and domain behavior.
- `integration/` contains persistent backends, external services, and cross-repo checks.
- `smoke/` contains short end-to-end product flows.
- `fixtures/` contains bounded, copyright-safe input payloads.
- `_helpers/` contains reusable test setup, markers, and assertions.

Keep real credentials, GPU/model loads, Docker orchestration, and long worker
soaks out of ordinary CI. Mark those tests `manual` and/or `slow`; fake
providers and in-memory backends belong in the default `ci` profile. The CI
workflow explicitly excludes `slow`, `manual`, `llm_real`, `longrun`, and
`requires_ollama`, even if a test is accidentally given an explicit `ci`
marker. Every CI run prints the slowest tests with `--durations`.
