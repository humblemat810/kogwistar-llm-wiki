# LLM-Wiki 0.5.11

## Release Status

- Version: `0.5.11`
- Parser dependency: `graph-knowledge-doc-parser>=0.2.13,<0.3`
- Parser release: `0.2.13`
- Parser pinned commit: `c65556e4df864a6033cffe5cb57ede7bdd43a8c0`

## Changes

- Align the application dependency with the merged parser `0.2.13` release.
- Update CI, PyPy, benchmark, and Docker publication workflows to use the
  immutable parser `0.2.13` commit.
- Preserve local parser validation for providers and gateways that do not
  enforce OpenAI-compatible JSON Schema response formats.

## Release Gate

Run the complete LLM-Wiki CI matrix before tagging or publishing `v0.5.11`.
Only update downstream pins after parser `0.2.13` is visible on PyPI and the
LLM-Wiki checks pass.
