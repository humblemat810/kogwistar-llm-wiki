# LLM-Wiki 0.5.10

## Highlights

- Aligns the application with the merged and published `kg-doc-parser` 0.2.9
  release.
- Updates every CI, PyPy, benchmark, and Docker publishing workflow to use the
  parser merge commit `6e764b6424a57b328fba572e6393f98319e701be`.
- Keeps the existing Kogwistar and parser compatibility contracts while
  ensuring release builds use the same parser revision as normal CI.

## Compatibility

This is a downstream patch release. The parser dependency is constrained to
`graph-knowledge-doc-parser>=0.2.9,<0.3`; CPython 3.12-3.14 and PyPy 3.11
remain supported according to the repository CI profiles.

## Release Gate

Publish only after this change is merged into `main` and required CI checks
pass. Create the tag from the merged `main` commit:

```text
v0.5.10
```

The Docker publishing workflows should then build the normal, all-adaptor, and
PyPy 3.11 image variants from that tagged commit.
