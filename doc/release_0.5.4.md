# LLM-Wiki 0.5.4

LLM-Wiki `0.5.4` is a patch release after plugin-boundary hardening. The base
application remains independent of optional channel-specific integrations.

## Release scope

- Bumps the package and application image version to `0.5.4`.
- Aligns CI and Docker builds with the merged Kogwistar, KG Doc Parser, and
  Obsidian sink revisions.
- Keeps the default and all-adapters images free of optional channel-plugin
  code and dependencies.
- Keeps source-specific parsers and ontologies outside the product host.

## Image targets

The release workflow publishes:

```text
profchan/kogwistar-llm-wiki:v0.5.4
profchan/kogwistar-llm-wiki:all-v0.5.4
```

The PyPy and embedding images remain separate workflow targets. Optional
channel integrations are installed separately and enabled explicitly by the
application.

## Pinned source revisions

The release workflows use immutable merged source revisions:

```text
Kogwistar:       3a9dfb8951175a35f7be9299208ac31d849c288f
KG Doc Parser:   70bc77f2a3a3f8b7c95be51a2605e3a0a33b1135
Obsidian sink:   2fcea33c34bfb2d09445fe587fa25f25ef8bc6d4
```
