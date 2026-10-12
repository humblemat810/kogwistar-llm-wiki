# LLM-Wiki 0.5.12

## Release Status

- Version: `0.5.12`
- Parser dependency: `graph-knowledge-doc-parser>=0.2.14,<0.3`
- Parser release: `0.2.14`
- Parser merged revision: `c65556e4df864a6033cffe5cb57ede7bdd43a8c0`
- Kogwistar release: `0.6.8`
- Kogwistar merged revision: `6d7a4d0cd852b6415165967c8ba130587956c7fb`

## Changes

- Align the runtime dependency with the published parser `0.2.14` release.
- Preserve the immutable parser checkout pin used by CI and release workflows.
- Keep the existing Kogwistar `0.6.8` dependency alignment.

## Release Gate

Run the complete LLM-Wiki CI matrix, including CPython 3.12-3.14 and PyPy
3.11, before tagging or publishing `v0.5.12`. Do not move or overwrite the
existing `v0.5.11` tag.
