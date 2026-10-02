# LLM-Wiki 0.5.5

LLM-Wiki `0.5.5` is a backward-compatible patch release that adds optional
bounded neighbor context to provider-backed background cross-link review.

## Release scope

- Bumps the package and application image version to `0.5.5`.
- Adds opt-in node, edge, token, and character budgets for cross-link context.
- Reuses Kogwistar context packing semantics with deterministic ordering.
- Filters workspace and ACL scope before context reaches a provider.
- Keeps authoritative source evidence and existing validation unchanged.
- Preserves the endpoint-only behavior when no context budget is supplied.

## Image targets

The release workflow publishes:

```text
profchan/kogwistar-llm-wiki:v0.5.5
profchan/kogwistar-llm-wiki:all-v0.5.5
```

The all-adapters image is gated by an import/build validation job before
publication. PyPy and embedding images remain separate workflow targets.

## Release process

After this release commit is merged into `main`, push the immutable tag:

```text
v0.5.5
```

The Docker workflow verifies that the tag matches `pyproject.toml`, validates
the all-adapters image, and publishes only after that validation succeeds.
