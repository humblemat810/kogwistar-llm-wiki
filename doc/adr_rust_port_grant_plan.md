# ADR Grant Plan — kogwistar-llm-wiki Rust Port (M5–M7)

## Decision
Execute the Rust port of `kogwistar-llm-wiki` (root repo, branch `rustport`, `rust/`) only. Do NOT rewrite `kogwistar-core` (llm-wiki Python source) unless a Rust-equivalent capability is genuinely missing. Do NOT touch vendored `kogwistar/`, `kg-doc-parser/`, obsidian source/sink, email-plugin unless a separate, explicit audit grants it.

## Granted scope (this repo)

| Area | Granted | Not granted |
|------|---------|-------------|
| `rust/` Rust port modules + tests | Yes (M1–M7) | — |
| `doc/rust_port_tracklist.md`, `doc/rust_parity_audit.md` | Yes | — |
| `src/kogwistar_llm_wiki/` Python source | No | Do not change llm-wiki Python |
| Vendored `kogwistar/`, `kg-doc-parser/`, obsidian source/sink, email-plugin | No (read-only audit only) | No changes without separate grant |

## Milestone plan

| Milestone | Scope | Target |
|-----------|-------|--------|
| M1–M4 | seam, worker strategy, recovery, workbench | done (prior) |
| M5 | larger parity slices (embedding, runtime, multimodal, grounding, archive, disambiguation, reconciliation, patch policy, codex bridge, compose TUI, daemon loops) | partly done; T5.5/8/12/14 pending contract discovery |
| M6 | parity audit + CI gates (fmt/clippy/test green) | audit doc written; CI green for current tree |
| M7 | version bump + PyPI publish + watch GitHub CI | pending |

## Commit convention
`feat(rust-port): add T5.x <name> (M5)` per mini-milestone; never push-and-forget; watch GitHub CI after push.

## What requires a separate grant before work
- Rust versions of obsidian source/sink and kg-doc-parser (three repos outside this repo).
- `kogwistar-obsidian-source` repo path not yet confirmed on disk; audit blocked.
- `kg-doc-parser` has multiple variants; canonical variant not yet confirmed.
- Changing `src/kogwistar_llm_wiki/` Python source (llm-wiki core).
