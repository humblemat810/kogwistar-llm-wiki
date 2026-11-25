# Rust Port

This workspace is the Rust port for the root `kogwistar-llm-wiki` repository.
It is intentionally separate from the vendored `kogwistar/` checkout and the
historical `experiments/dual_language_seam_poc` proof of concept.

## Current surface

- `kogwistar-llm-wiki-port::configuration::workspace` mirrors the app-level
  graph-space enum, workspace namespace helpers, namespace metadata, and
  workspace/graph-space metadata matcher from
  `src/kogwistar_llm_wiki/configuration/workspace.py`.
- `kogwistar-llm-wiki-port::models` mirrors the ingest, maintenance,
  Obsidian-build, and projection snapshot contract models from
  `src/kogwistar_llm_wiki/models.py`, including Python wire names and defaults.
- `kogwistar-llm-wiki-port::projections` mirrors the bounded projection
  snapshot helpers from `src/kogwistar_llm_wiki/projections/snapshot.py`:
  graph-space normalization, supported graph-space namespaces, manifest-based
  node visibility, and bidirectional adjacency construction.

## Commands

From this directory:

```powershell
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The port is incremental. Prefer one Python contract slice at a time, keep the
wire names stable, and add a Rust test for each behavior before expanding
another module.
