# LLM-Wiki 0.5.8

## Highlights

- Bumps the application and image version to `0.5.8`.
- Pins every CI, PyPy, benchmark, and Docker publication workflow to the
  merged and published `kg-doc-parser` `0.2.6` commit
  `6110e0c3bdb1bb3cac7fd9bdbfe4b6b3978201e9`.
- Requires the published parser distribution in the compatible `>=0.2.6,<0.3`
  range while retaining the local editable source used by repository tooling.
- Keeps parser behavior and ownership in `kg-doc-parser`; LLM-Wiki only updates
  its downstream dependency and reproducible source pin.

## Compatibility

The parser remains independently versioned. This release changes the
downstream parser pin and package constraint only; it does not add parser
implementation code to LLM-Wiki.

## Release Gate

Publish only after this branch is merged into `main` and the full LLM-Wiki CI
matrix passes on CPython 3.12-3.14 and PyPy 3.11. The release tag must match
the package version:

```text
v0.5.8
```

The Docker workflows must verify the tag and build the normal and all-adapters
images from that merged tag using the pinned parser revision above.
