# LLM-Wiki 0.5.4

LLM-Wiki `0.5.4` is a patch release after the merged email-boundary hardening
work. The application image remains intentionally independent of the optional
`kogwistar-email-plugin` repository.

## Release scope

- Bumps the package and application image version to `0.5.4`.
- Aligns CI and Docker builds with the merged Kogwistar, KG Doc Parser, and
  Obsidian sink revisions.
- Keeps the default and all-adapters images free of email-plugin code and
  dependencies.
- Keeps the email ontology/parser as a separately installed downstream plugin.

## Image targets

The release workflow publishes:

```text
profchan/kogwistar-llm-wiki:v0.5.4
profchan/kogwistar-llm-wiki:all-v0.5.4
```

The PyPy and embedding images remain separate workflow targets. The email
plugin is not copied into any LLM-Wiki image; install it separately only when
the email topology is required.

## Pinned source revisions

The release workflows use immutable merged source revisions:

```text
Kogwistar:       3a9dfb8951175a35f7be9299208ac31d849c288f
KG Doc Parser:   dec9d958009c960785167ccd4b9f069ec0c13e48
Obsidian sink:   2fcea33c34bfb2d09445fe587fa25f25ef8bc6d4
```
