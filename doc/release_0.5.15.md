# LLM-Wiki 0.5.15

## Release Status

- Version: `0.5.15`
- Parser dependency: `graph-knowledge-doc-parser>=0.2.16,<0.3`
- Parser release: `0.2.16`
- Parser pinned commit: `ebcbb407462f1e7b9b353cd8fe8c1cfdaab140ef`
- Kogwistar release: `0.6.8`

## Changes

- Consume the published KG Doc Parser `0.2.16` release.
- Align normal CI, full CI, PyPy, benchmark, container, and Docker publication
  checkouts with the merged parser `v0.2.16` commit.
- Verify the parser's source-pointer rehydration and structural-invariant
  regression coverage through the downstream integration paths.

The Obsidian source remains an optional external package and is not bundled in
the standard runtime or Docker image. The existing Obsidian sink remains the
one-way projection exporter.

## Release Gate

Run the complete LLM-Wiki CI matrix, including CPython 3.12-3.14 and PyPy
3.11, before merging or tagging `v0.5.15`. Publish images only from the merged
tag after the release verification workflow succeeds.
