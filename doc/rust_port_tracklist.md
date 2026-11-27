# Rust Port Tracklist � kogwistar-llm-wiki

Root repo: C:\Users\chanh\Documents\kogwistar-llm-wiki (branch rustport)
Rust workspace: rust/
Scope: root repo rust port only. Do NOT touch vendored kogwistar/, kg-doc-parser/, obsidian source/sink, email-plugin.

CI baseline (from rust/): cargo fmt --all -- --check, cargo clippy --workspace --all-targets -- -D warnings, cargo test --workspace all pass.

Python fallback contracts live in src/kogwistar_llm_wiki/. Each Rust slice mirrors a Python contract and adds a Rust test before expanding the next module.

## Coarse worklist

| Milestone | Scope | Status |
|-----------|-------|--------|
| M1 | Commit existing PyO3 seam + maintenance_patch_validation (graph_space_match, patch validation, port lib.rs/README wiring) | done |
| M2 | Maintenance worker graph-patch strategy inputs & accepted-candidate replacement contracts (Rust + tests mirroring Python) | done |
| M3 | Recovery isolation: checkpoint fallback, service-health repair actions, workspace-filtered run-history contracts | done |
| M4 | Workbench: bounded lens projections, semantic-lens snapshots, cockpit action/patch proposal validation, review artifacts | done |
| M5 | Larger parity slices: embedding adapters, runtime contracts, multimodal projections, grounding, source-unit handling, archive restore/event-chain, disambiguation, service ops, reconciliation, patch policy, codex bridge, structured runner, compose TUI, project-memory APIs, daemon/projection/maintenance lifecycle loops | next |
| M6 | Full rust parity audit + CI gates (fmt/clippy/test green) | pending |
| M7 | Version bump + publish new package to PyPI so downstream can pip install | pending |

## Fine working tracklist

Each tasklet: ID | description | Rust file(s) | test gate | status

### M1 � commit existing seam (done)
- T1.1 Port graph_space_match JSON seam into python crate lib.rs + native.py fallback + __all__ export | rust/crates/kogwistar-llm-wiki-python/src/lib.rs, src/kogwistar_llm_wiki/native.py | tests/unit/test_native_facade.py::test_graph_space_match_fallback_matches_python + cargo test | done
- T1.2 Add maintenance_patch_validation module to port lib.rs + README surface list | rust/crates/kogwistar-llm-wiki-port/src/lib.rs, rust/README.md | cargo test --workspace | done
- T1.3 Add maintenance_patch_validation source (graph patch idempotency, provenance, namespace, endpoint, supersession checks) | rust/crates/kogwistar-llm-wiki-port/src/maintenance_patch_validation.rs | new Rust test file | done
- T1.4 Add maintenance_patch_validation Rust tests (missing tombstone target code, idempotency/provenance/namespace/endpoint/supersession) | rust/crates/kogwistar-llm-wiki-port/tests/maintenance_patch_validation.rs | cargo test maintenance_patch_validation | done
- T1.5 Commit T1.1�T1.4 in mini-milestones (separate commits for seam vs patch validation) | git | git log shows two clean commits | done

### M2 � maintenance worker graph-patch strategy inputs & accepted-candidate replacement (done)
- T2.1 Add graph-patch strategy input structs (GraphPatchStrategyInput, AcceptedCandidate, ReplacementContract) to maintenance_worker.rs | rust/crates/kogwistar-llm-wiki-port/src/maintenance_worker.rs | compiles, clippy clean | done
- T2.2 Wire accepted-candidate graph_patch patch-payload extraction into worker input construction | maintenance_worker.rs | patch_payload only for graph_patch kind | done
- T2.3 Add accepted-candidate replacement contract: reject source-native tombstone, preserve endpoints, default replacement reason | maintenance_worker.rs | unit test: valid contract + tombstone rejection | done
- T2.4 Add Rust tests mirroring Python worker_execution.py accepted-candidate graph_patch flow + replacement rules | tests/maintenance_worker.rs | cargo test maintenance_worker | done
- T2.5 Commit M2 as one mini-milestone | git | clean commit, cargo test --workspace green | done

### M3 � recovery isolation (done)
- T3.1 Checkpoint fallback contract (save/restore bounded checkpoint, invalid-checkpoint rejection) | new module maintenance_checkpoint.rs | Rust test: fallback restores last valid checkpoint | done
- T3.2 Service-health repair actions (healthy/degraded/unhealthy classification, repair trigger) | maintenance_repair.rs or extend maintenance_state.rs | Rust test: repair action selection | done
- T3.3 Workspace-filtered run-history contract (list run history filtered by workspace id, bounded page) | maintenance_run_history.rs | Rust test: workspace filter excludes other workspaces | done
- T3.4 Commit M3 | git | clean commit, cargo test --workspace green | done

### M4 � workbench
- T4.1 Bounded lens projections (project graph slice within lens bounds) | new module lens_projection.rs | Rust test: bound enforcement | done
- T4.2 Semantic-lens snapshots (deterministic snapshot id + restore) | semantic_lens.rs | Rust test: snapshot round-trip | done
- T4.3 Cockpit action/patch proposal validation (validate proposal before apply) | extend crosslink_proposals.rs or new cockpit.rs | Rust test: invalid proposal rejected | done
- T4.4 Review artifacts (durable review batch, CAS outcome, decision metadata) | extend crosslink_reviews.rs | Rust test: review batch CAS | pending
- T4.5 Commit M4 | git | clean commit, green | pending

### M5 � larger parity slices (one tasklet per slice, tests mirror Python)
- T5.1 Embedding adapters | embedding_adapter.rs | Rust test: adapter output matches Python | done
- T5.2 Runtime contracts | runtime_contract.rs | Rust test: wire shape matches Python | done
- T5.3 Multimodal projections | multimodal_projection.rs | Rust test: projection matches Python | pending
- T5.4 Grounding | grounding.rs | Rust test: grounding digest matches Python | done
- T5.5 Source-unit handling | source_unit.rs | Rust test: unit boundaries match Python | pending
- T5.6 Archive restore / event-chain | archive_restore.rs, event_chain.rs | Rust test: restore replay matches Python | pending
- T5.7 Disambiguation | disambiguation.rs | Rust test: disambiguation decision matches Python | pending
- T5.8 Service ops | service_ops.rs | Rust test: op classification matches Python | pending
- T5.9 Reconciliation | reconciliation.rs | Rust test: reconciliation result matches Python | pending
- T5.10 Patch policy | patch_policy.rs | Rust test: policy decision matches Python | pending
- T5.11 Codex bridge | codex_bridge.rs | Rust test: bridge payload matches Python | pending
- T5.12 Structured runner | structured_runner.rs | Rust test: runner envelope matches Python | pending
- T5.13 Compose TUI | compose_tui.rs | Rust test: text validation matches Python | pending
- T5.14 Project-memory APIs | project_memory.rs | Rust test: memory CRUD matches Python | pending
- T5.15 Daemon/projection/maintenance lifecycle loops | daemon_loop.rs, projection_loop.rs, maintenance_loop.rs | Rust test: loop termination/bound matches Python | pending
- T5.16 Commit M5 | git | clean commit, green | pending

### M6 � parity audit + CI gates
- T6.1 Audit every Python contract in src/kogwistar_llm_wiki/ against Rust port; record gaps | doc/rust_parity_audit.md | audit doc complete | pending
- T6.2 Implement any missing parity slices surfaced by audit | per gap | Rust test per gap | pending
- T6.3 cargo fmt --all -- --check + cargo clippy --workspace --all-targets -- -D warnings + cargo test --workspace all green | CI | 0 failures | pending
- T6.4 Python facade tests pass (pytest tests/unit/test_native_facade.py -p no:cacheprovider) | Python | 0 failures | pending
- T6.5 Commit M6 | git | clean commit | pending

### M7 � release
- T7.1 Bump version in rust/Cargo.toml + python crate Cargo.toml + root pyproject.toml | version files | versions consistent | pending
- T7.2 Add release note / changelog entry | doc/ | changelog updated | pending
- T7.3 Publish new package to PyPI | publish | downstream pip install works | pending
- T7.4 Commit M7 + watch GitHub CI for errors | git + CI | CI green | pending

## Progress conventions
- Mark a tasklet done only after its Rust test + cargo test passes.
- Commit each milestone as one or more mini-milestones; never push-and-forget � watch GitHub CI.
- Keep wire names stable with Python; add a Rust test for each behavior before expanding the next module.
