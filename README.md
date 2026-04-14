# Kogwistar LLM-Wiki

# this also is a dev experiment -> open source, let chatgpt see the opensource and dev in its own sandbox.

Docs-first workspace for the LLM-Wiki orchestration layer built around Kogwistar.

## Install

Default consumer install:

```bash
uv pip install -e ".[dev,test]"
```

This keeps the default dependency source GitHub-first for `kogwistar`, `graph-knowledge-doc-parser`, and `kogwistar-obsidian-sink`.

## Opt-In Local Bootstrap

Run the bootstrap only when you want local editable checkouts of the sibling repos:

```bash
bash scripts/bootstrap-dev.sh
```

What it does:

- clones `./kogwistar`, `./kg-doc-parser`, and `./kogwistar-obsidian-sink` from GitHub if they are missing
- installs the local checkouts editable into the active environment

The bootstrap is manual and opt-in. It does not run during normal installs.

Windows users can run the same script from Git Bash or WSL.

## Docs

- [Development setup](doc/dev_setup_guide.md)
- [Environment cheat sheet](doc/environment_cheatsheet.md)
- [Architecture](doc/architecture.md)
- [Core workflows](doc/core_workflows.md)
