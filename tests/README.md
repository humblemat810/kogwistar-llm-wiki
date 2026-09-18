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
providers and in-memory backends belong in the default `ci` profile.
