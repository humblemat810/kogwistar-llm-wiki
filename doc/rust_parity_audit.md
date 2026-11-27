# Rust Parity Audit — kogwistar-llm-wiki (M5/M6)

Scope: root repo rust port only (`rust/`). Do NOT touch vendored `kogwistar/`, `kg-doc-parser/`, obsidian source/sink, email-plugin. llm-wiki Python source is unchanged; Rust port adds mirror modules + tests.

CI baseline (from `rust/`): `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace` all pass.

## M1–M4 (committed, prior)

| Milestone | Scope | Status |
|-----------|-------|--------|
| M1 | PyO3 seam + maintenance_patch_validation | done |
| M2 | maintenance worker graph-patch strategy + accepted-candidate replacement | done |
| M3 | recovery isolation: checkpoint, service-health repair, workspace-filtered run-history | done |
| M4 | workbench: lens projections, semantic-lens snapshots, cockpit validation, review artifacts | T4.4 pending (review artifacts) |

## M5 larger parity slices (one tasklet per slice, tests mirror Python)

| ID | Slice | Rust file(s) | Python contract | Status | Note |
|----|-------|--------------|-----------------|--------|------|
| T5.1 | Embedding adapters | embedding_adapter.rs | done | done |
| T5.2 | Runtime contracts | runtime_contract.rs | done | done |
| T5.3 | Multimodal projections | multimodal_projection.rs | done | done |
| T5.4 | Grounding | grounding.rs | done | done |
| T5.5 | Source-unit handling | source_unit.rs | agent/source_tools.py | done | committed 47a71dc |
| T5.6 | Archive restore / event-chain | archive_restore.rs, event_chain.rs | archiving/archive_contracts.py | done | committed 878771c |
| T5.7 | Disambiguation | disambiguation.rs | done | done |
| T5.8 | Service ops | service_ops.rs | maintenance/maintenance_status.py | done | committed 1445e18 |
| T5.9 | Reconciliation | reconciliation.rs | done | done |
| T5.10 | Patch policy | patch_policy.rs | maintenance/maintenance_policy.py | done | committed c7a8ac0 |
| T5.11 | Codex bridge | codex_bridge.rs | codex/codex_bridge.py | done | committed f898e2d |
| T5.12 | Structured runner | structured_runner.rs | codex/codex_workbench_agent.py | done | committed 7be7c55 |
| T5.13 | Compose TUI | compose_tui.rs | codex/codex_compose_tui.py | done | committed 05e4020 |
| T5.14 | Project-memory APIs | project_memory.rs | memory/service.py | done | committed 7f6773a |
| T5.15 | Daemon/projection/maintenance loops | daemon_loop.rs | daemon.py, daemons/ | done | committed e43959e |
| T5.16 | Commit M5 | git | done | M5 complete, all T5.* done |
| T5.17 | Parse reconciliation | parse_reconciliation.rs | parsing/parse_reconciliation.py | done | TDD slice; decisions and confidence gate mirrored |
| T5.18 | Application message envelope | app_contracts.rs | app_contracts/messaging.py | done | TDD slice; DTO slice excludes backend-only trace field |
| T5.19 | Obsidian source request identity | app_contracts.rs | app_contracts/obsidian_source.py | done | TDD slice; bounded workspace/vault identity validated |

## M6 parity audit + CI gates

| ID | Work | Status |
|----|------|--------|
| T6.1 | Audit every Python contract in `src/kogwistar_llm_wiki/` against Rust port; record gaps | this doc |
| T6.2 | Implement missing parity slices surfaced by audit | in progress (see `rust_contract_inventory.md`) |
| T6.3 | `cargo fmt --all -- --check` + `cargo clippy ... -D warnings` + `cargo test --workspace` all green | done (current tree) |
| T6.4 | Python facade tests pass | done | 7 passed, `tests/unit/test_native_facade.py -p no:cacheprovider` |
| T6.5 | Commit M6 | pending |

## Gaps requiring contract discovery (no same-named Python contract)

None remaining. All T5.* slices have Rust modules with tests:
- T5.5 source_unit → agent/source_tools.py (committed 47a71dc)
- T5.8 service_ops → maintenance/maintenance_status.py (committed 1445e18)
- T5.12 structured_runner → codex/codex_workbench_agent.py (committed 7be7c55)
- T5.14 project_memory → memory/service.py (committed 7f6773a)

## Three-repo parity audit (kg-doc-parser, obsidian-source, obsidian-sink)

- `kogwistar-obsidian-sink`: present under `C:\Users\chanh\Documents\kogwistar-obsidian-sink`.
- `kg-doc-parser`: multiple variants present (`kg_doc_parser`, `kg_doc_parser_current`, `kg-doc-parser-bonsai-pin`, ...); canonical variant not yet confirmed.
- `kogwistar-obsidian-source`: NOT found under `C:\Users\chanh\Documents`; audit of this repo is blocked until the repo path is confirmed.

Parity audit of the three repos is read-only (no changes to llm-wiki). It requires confirming repo paths and extracting each repo's Python contracts, then mirroring missing Rust equivalents without rewriting `kogwistar-core` unless a capability is genuinely missing.

## CI result (current tree)

`cargo fmt --all -- --check`: pass
`cargo clippy --workspace --all-targets -- -D warnings`: pass
`cargo test --workspace`: pass

## Commits (M5, this session)

- f898e2d feat(rust-port): add T5.11 codex_bridge (M5)
- 878771c feat(rust-port): add T5.6 archive restore + event chain (M5)
- 05e4020 feat(rust-port): add T5.13 compose tui contracts (M5)
- e43959e feat(rust-port): add T5.15 daemon loop primitives (M5)
- c7a8ac0 feat(rust-port): add T5.10 patch policy (M5)
