# LLM-Wiki 0.5.7

## Highlights

- Bumps the application and image version to `0.5.7`.
- Aligns every CI, PyPy, benchmark, and Docker publication workflow with the
  merged and published `kg-doc-parser` `0.2.5` release.
- Pins the parser source to immutable commit
  `ea845d0219a802b64f38421dacef7f190b743dfd`, which is the merged `v0.2.5`
  commit.
- Keeps the portable Bonsai documentation changes from this branch.

## Compatibility

The parser remains an independently versioned dependency. This release changes
the downstream source pin only; it does not add parser-specific runtime code to
LLM-Wiki or change the parser's public contracts.

## Release Gate

Publish only after this branch is merged into `main` and the full LLM-Wiki CI
matrix passes on CPython 3.12-3.14 and PyPy 3.11. The release tag must match
the package version:

```text
v0.5.7
```

The Docker workflow must verify the tag and build the normal and all-adapters
images from that merged tag.
