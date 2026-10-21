# LLM-Wiki 0.5.16

## Release Status

- Version: `0.5.16`
- Parser dependency: `graph-knowledge-doc-parser>=0.2.17,<0.3`
- Parser release: `0.2.17`
- Parser pinned commit: `9b489b030034cc7626dcd053fd70ca56d09e3af0`
- Kogwistar release: `0.6.15`
- Kogwistar pinned commit: `b1ef37d006980286bea62b371083b816ec949d14`

## Changes

- Consume KG Doc Parser `0.2.17`, including the merged OCR workflow typing and
  modularization updates.
- Pin Kogwistar `0.6.15` so Codex bridge image content parts remain structured
  through the provider adapter.
- Align normal CI, full CI, PyPy, benchmark, container, and Docker publication
  checkouts with the exact merged parser commit.
- Keep the optional Obsidian source package out of the standard runtime and
  Docker image; retain the Obsidian sink as the one-way projection exporter.

## Release Gate

Kogwistar `0.6.15` and KG Doc Parser `0.2.17` are both published. Run the full
LLM-Wiki CI matrix, including CPython 3.12-3.14 and PyPy 3.11, against these
exact commits before merging. Publish images only from the merged `v0.5.16` tag
after release verification succeeds.
