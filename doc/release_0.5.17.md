# LLM-Wiki 0.5.17

## Release Status

- Version: `0.5.17`
- Rust port workspace: `0.2.0` (crates `kogwistar-llm-wiki-port` and `kogwistar-llm-wiki-python`)
- Rust edition: `2021`, rust-version `1.83`

## Changes

- Complete M5 rust port parity slices: embedding adapters, runtime contracts,
  multimodal projections, grounding, source-unit handling, archive restore +
  event chain, disambiguation, service ops, reconciliation, patch policy, codex
  bridge, structured runner, compose TUI, project-memory APIs, daemon/projection/
  maintenance lifecycle loops.
- Complete M6 parity audit + CI gates: every Python contract in
  `src/kogwistar_llm_wiki/` audited against the Rust port; gaps recorded and
  implemented; `cargo fmt --all -- --check`, `cargo clippy --workspace
  --all-targets -- -D warnings`, and `cargo test --workspace` all green; Python
  facade tests pass (7/7).
- All T5.* and M5/M6 tasklets committed on branch `rustport`.

## Release Gate

Run `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`,
and `cargo test --workspace` from `rust/`; run `pytest tests/unit/test_native_facade.py
-p no:cacheprovider` from the repo root. Publish images only from the merged
`v0.5.17` tag after release verification succeeds.
