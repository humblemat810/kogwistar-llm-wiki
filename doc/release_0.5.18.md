# LLM-Wiki 0.5.18  
  
## Release Status  
  
- Version: \`0.5.18\`  
- Rust port workspace: \`0.3.0\` (crates \`kogwistar-llm-wiki-port\` and \`kogwistar-llm-wiki-python\`)  
- Rust edition: \`2021\`, rust-version \`1.83\`  
  
## Changes  
  
- Complete M5 rust port parity slices: embedding adapters, runtime contracts, multimodal projections, grounding, source-unit handling, archive restore + event chain, disambiguation, service ops, reconciliation, patch policy, codex bridge, structured runner, compose TUI, project-memory APIs, daemon/projection/maintenance lifecycle loops.  
- Complete M6 parity audit + CI gates: the Python-to-Rust contract inventory is grounded in \`doc/rust_contract_inventory.md\`; all pure-contract gaps implemented; \`cargo fmt --all -- --check\`, \`cargo clippy --workspace --all-targets -- -D warnings\`, and \`cargo test --workspace\` all green; Python facade tests pass (7/7).  
- All T5.* and M5/M6 tasklets committed on branch \`rustport\`.  
- Version bump: Python \`0.5.17\` \u2192 \`0.5.18\`, Rust crates \`0.2.0\` \u2192 \`0.3.0\`.  
  
## Release Gate  
  
Run \`cargo fmt --all -- --check\`, \`cargo clippy --workspace --all-targets -- -D warnings\`, and \`cargo test --workspace\` from \`rust/\`; run \`pytest tests/unit/test_native_facade.py -p no:cacheprovider\` from the repo root. The \`v0.5.18\` tag also runs the PyPI release workflow; publish images only after release verification succeeds. 
