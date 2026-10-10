# LLM-Wiki 0.5.9

## Highlights

- Bumps the application and image version to `0.5.9`.
- Completes the cross-repository type-contract hardening across LLM-Wiki,
  Kogwistar, and KG Doc Parser without changing persisted graph or parser
  contracts.
- Keeps the runtime compatible with CPython 3.12-3.14 and the supported PyPy
  3.11 Python-authority profile.
- Preserves the merged dependency pins used by the reproducible image builds:
  Kogwistar `e9ade2975e54be7925865555209ccbdea88cc6ae`, KG Doc Parser
  `6110e0c3bdb1bb3cac7fd9bdbfe4b6b3978201e9`, and Obsidian Sink
  `36f51e5c23522cd3e467ee49bc7127461b4753c2`.

## Compatibility

This release is intended to be behavior-preserving. The changes narrow type
contracts, retain the existing public APIs, and keep optional PyPy 3.12 beta
coverage non-blocking. The PyPy 3.12 beta lane is an experimental diagnostic
profile and is not a runtime support or publication gate.

## Release Gate

Publish only after this branch is merged into `main` and the required GitHub
CI checks pass on CPython 3.12-3.14 and PyPy 3.11, including lint, Rust checks,
and the container smoke gate. The release tag must match the package version:

```text
v0.5.9
```

The tag-triggered Docker workflow must first validate the all-adapters image,
then publish the normal and all-adapters application images. The PyPy 3.11
native image is published only by its separate post-CI workflow. Do not create
the tag from this feature branch; create it from the merged `main` commit.
