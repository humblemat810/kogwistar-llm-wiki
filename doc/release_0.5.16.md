# LLM-Wiki 0.5.16

## Release Status

- Version: `0.5.16`
- Parser dependency: `graph-knowledge-doc-parser>=0.2.17,<0.3`
- Parser release: `0.2.17`
- Parser pinned commit: `76cdc4f2595a4425523825d05abb0a0fe67cdd85`
- Kogwistar release: `0.6.14`
- Kogwistar pinned commit: `cbe4ea9edc0877cb0a419c7ba17302a33203e7f6`

## Changes

- Consume KG Doc Parser `0.2.17`, including the merged OCR workflow typing and
  modularization updates.
- Pin Kogwistar `0.6.14` so Codex bridge image content parts remain structured
  through the provider adapter.
- Align normal CI, full CI, PyPy, benchmark, container, and Docker publication
  checkouts with the exact merged parser commit.
- Keep the optional Obsidian source package out of the standard runtime and
  Docker image; retain the Obsidian sink as the one-way projection exporter.

## Release Gate

The parser `0.2.17` commit has merged, but the package must be tagged and
published to PyPI before this release is merged or tagged. Then run the full
LLM-Wiki CI matrix, including CPython 3.12-3.14 and PyPy 3.11. Publish images
only from the merged `v0.5.16` tag after release verification succeeds.
