# LLM-Wiki 0.5.14

## Release Status

- Version: `0.5.14`
- Parser dependency: `graph-knowledge-doc-parser>=0.2.15,<0.3`
- Parser release: `0.2.15`
- Parser pinned commit: `d59f7ad5044df8c059f22539f1fef973a713d531`
- Kogwistar release: `0.6.8`

## Changes

- Consume the published KG Doc Parser `0.2.15` release.
- Align all normal CI, PyPy, benchmark, container, and Docker publication
  checkouts with the merged parser `v0.2.15` commit.
- Include the parser's structural-invariant hardening and authoritative pointer
  validation in the downstream test and image build path.

The Obsidian source remains an optional external package and is not bundled in
the standard runtime or Docker image. The existing Obsidian sink remains the
one-way projection exporter.

## Release Gate

Run the complete LLM-Wiki CI matrix, including CPython 3.12-3.14 and PyPy
3.11, before merging or tagging `v0.5.14`. Publish images only from the merged
tag after the release verification workflow succeeds.
