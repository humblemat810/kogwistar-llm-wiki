# Rust Port Tracklist ï¿½ kogwistar-llm-wiki

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
| M6 | Full rust parity audit + CI gates (fmt/clippy/test green) | done |
| M7 | Version bump + publish new package to PyPI so downstream can pip install | pending |
| M9 | End-to-end Rust CLI + MCP server pathway: command parser, dispatch contracts, MCP tool registry/auth/transport, daemon/longrun/otel support | in_progress |

## Fine working tracklist

Each tasklet: ID | description | Rust file(s) | test gate | status

### M1 ï¿½ commit existing seam (done)
- T1.1 Port graph_space_match JSON seam into python crate lib.rs + native.py fallback + __all__ export | rust/crates/kogwistar-llm-wiki-python/src/lib.rs, src/kogwistar_llm_wiki/native.py | tests/unit/test_native_facade.py::test_graph_space_match_fallback_matches_python + cargo test | done
- T1.2 Add maintenance_patch_validation module to port lib.rs + README surface list | rust/crates/kogwistar-llm-wiki-port/src/lib.rs, rust/README.md | cargo test --workspace | done
- T1.3 Add maintenance_patch_validation source (graph patch idempotency, provenance, namespace, endpoint, supersession checks) | rust/crates/kogwistar-llm-wiki-port/src/maintenance_patch_validation.rs | new Rust test file | done
- T1.4 Add maintenance_patch_validation Rust tests (missing tombstone target code, idempotency/provenance/namespace/endpoint/supersession) | rust/crates/kogwistar-llm-wiki-port/tests/maintenance_patch_validation.rs | cargo test maintenance_patch_validation | done
- T1.5 Commit T1.1ï¿½T1.4 in mini-milestones (separate commits for seam vs patch validation) | git | git log shows two clean commits | done

### M2 ï¿½ maintenance worker graph-patch strategy inputs & accepted-candidate replacement (done)
- T2.1 Add graph-patch strategy input structs (GraphPatchStrategyInput, AcceptedCandidate, ReplacementContract) to maintenance_worker.rs | rust/crates/kogwistar-llm-wiki-port/src/maintenance_worker.rs | compiles, clippy clean | done
- T2.2 Wire accepted-candidate graph_patch patch-payload extraction into worker input construction | maintenance_worker.rs | patch_payload only for graph_patch kind | done
- T2.3 Add accepted-candidate replacement contract: reject source-native tombstone, preserve endpoints, default replacement reason | maintenance_worker.rs | unit test: valid contract + tombstone rejection | done
- T2.4 Add Rust tests mirroring Python worker_execution.py accepted-candidate graph_patch flow + replacement rules | tests/maintenance_worker.rs | cargo test maintenance_worker | done
- T2.5 Commit M2 as one mini-milestone | git | clean commit, cargo test --workspace green | done

### M3 ï¿½ recovery isolation (done)
- T3.1 Checkpoint fallback contract (save/restore bounded checkpoint, invalid-checkpoint rejection) | new module maintenance_checkpoint.rs | Rust test: fallback restores last valid checkpoint | done
- T3.2 Service-health repair actions (healthy/degraded/unhealthy classification, repair trigger) | maintenance_repair.rs or extend maintenance_state.rs | Rust test: repair action selection | done
- T3.3 Workspace-filtered run-history contract (list run history filtered by workspace id, bounded page) | maintenance_run_history.rs | Rust test: workspace filter excludes other workspaces | done
- T3.4 Commit M3 | git | clean commit, cargo test --workspace green | done

### M4 ï¿½ workbench
- T4.1 Bounded lens projections (project graph slice within lens bounds) | new module lens_projection.rs | Rust test: bound enforcement | done
- T4.2 Semantic-lens snapshots (deterministic snapshot id + restore) | semantic_lens.rs | Rust test: snapshot round-trip | done
- T4.3 Cockpit action/patch proposal validation (validate proposal before apply) | extend crosslink_proposals.rs or new cockpit.rs | Rust test: invalid proposal rejected | done
- T4.4 Review artifacts (durable review batch, CAS outcome, decision metadata) | extend crosslink_reviews.rs | Rust test: review batch CAS | done; cargo test --package kogwistar-llm-wiki-port --test crosslink_reviews 6 passed
- T4.5 Commit M4 | git | clean commit, green | done

### M5 ï¿½ larger parity slices (one tasklet per slice, tests mirror Python)
- T5.1 Embedding adapters | embedding_adapter.rs | Rust test: adapter output matches Python | done
- T5.2 Runtime contracts | runtime_contract.rs | Rust test: wire shape matches Python | done
- T5.3 Multimodal projections | multimodal_projection.rs | Rust test: projection matches Python | done
- T5.4 Grounding | grounding.rs | Rust test: grounding digest matches Python | done
- T5.5 Source-unit handling | source_unit.rs | Rust test: unit boundaries match Python | done
- T5.6 Archive restore / event-chain | archive_restore.rs, event_chain.rs | Rust test: restore replay matches Python | done
- T5.7 Disambiguation | disambiguation.rs | Rust test: disambiguation decision matches Python | done
- T5.8 Service ops | service_ops.rs | Rust test: op classification matches Python | done
- T5.9 Reconciliation | reconciliation.rs | Rust test: reconciliation result matches Python | done
- T5.10 Patch policy | patch_policy.rs | Rust test: policy decision matches Python | done
- T5.11 Codex bridge | codex_bridge.rs | Rust test: bridge payload matches Python | done
- T5.12 Structured runner | structured_runner.rs | Rust test: runner envelope matches Python | done
- T5.13 Compose TUI | compose_tui.rs | Rust test: text validation matches Python | done
- T5.14 Project-memory APIs | project_memory.rs | Rust test: memory CRUD matches Python | done
- T5.15 Daemon/projection/maintenance lifecycle loops | daemon_loop.rs | Rust test: loop termination/bound matches Python | done
- T5.16 Commit M5 | git | clean commit, green | done
- T5.17 Parse reconciliation | parse_reconciliation.rs | Rust test: additive/review/replacement/lower-confidence decisions mirror Python | done
- T5.18 Application message envelope | app_contracts.rs | Rust test: public DTO slice excludes internal trace field | done
- T5.19 Obsidian source request identity | app_contracts.rs | Rust test: identity values are strings, nonblank, and bounded | done

### M6 ï¿½ parity audit + CI gates
- T6.1 Audit every Python contract in src/kogwistar_llm_wiki/ against Rust port; record gaps | doc/rust_contract_inventory.md | inventory complete | done
- T6.2 Implement any missing parity slices surfaced by audit | per gap | Rust test per gap | in progress
- T6.3 cargo fmt --all -- --check + cargo clippy --workspace --all-targets -- -D warnings + cargo test --workspace all green | CI | 0 failures | done
- T6.4 Python facade tests pass (pytest tests/unit/test_native_facade.py -p no:cacheprovider) | Python | 0 failures | done
- T6.5 Commit M6 | git | clean commit | done

### M7 ï¿½ release
- T7.1 Bump version in rust/Cargo.toml + python crate Cargo.toml + root pyproject.toml | version files | versions consistent | pending
- T7.2 Add release note / changelog entry | doc/ | changelog updated | done
- T7.3 Publish new package to PyPI | publish | downstream pip install works | pending
- T7.4 Commit M7 + watch GitHub CI for errors | git + CI | CI green | pending

## Progress conventions
- Mark a tasklet done only after its Rust test + cargo test passes.
- Commit each milestone as one or more mini-milestones; never push-and-forget ï¿½ watch GitHub CI.
- Keep wire names stable with Python; add a Rust test for each behavior before expanding the next module.








## M8 ≒ maintenance durable stores + embeddings remote I/O (tracking)

Scope mirrors Python fallback contracts under `src/kogwistar_llm_wiki/maintenance/` and `src/kogwistar_llm_wiki/embeddings/`.

| Tasklet | Description | Rust file(s) | Test gate | Status |
|---|---|---|---|---|
| T8.1.4 | Parsing session store: scope id, CAS save/activate, workspace + frontier pointer validation | src/parsing/session_store.rs, tests/session_store.rs | 10 tests pass; clippy clean | done |
| T8.1.5 | Maintenance durable stores: and_where, belongs_to_workspace, metadata_mapping, semantic_fingerprint, persisted_budget_state, maintenance_budget_state, durable_maintenance_usage, edge_ids; converts src/maintenance.rs -> src/maintenance/mod.rs | src/maintenance/mod.rs, src/maintenance/durable_stores.rs, tests/durable_stores.rs | new Rust tests mirror Python maintenance/state.py; cargo test green | planned |
| T8.1.6 | Embeddings remote I/O: EmbeddingServiceError/Unavailable/ProtocolError, HttpResponseLike, UrlOpener, EmbeddingServiceSettings, RemoteMultimodalEncoder over crate::multimodal_projection | src/embeddings/mod.rs, src/embeddings/remote.rs, tests/embeddings_remote.rs | deterministic fake-UrlOpener tests; cargo test green | done |
| T8.1.7 | Commit M8 mini-milestones; fmt/clippy/test green | git + CI | clean commits, workspace green | done |

Progress conventions: mark done only after its Rust test + `cargo test --workspace` passes; one small commit per tasklet; watch CI after push.


## M9 → end-to-end Rust CLI + MCP server pathway

Normal Python entrypoints remain under `src/kogwistar_llm_wiki/cli/` and `src/kogwistar_llm_wiki/agent/mcp_server.py`. This milestone adds Rust equivalents without changing llm-wiki Python contracts.

| Tasklet | Description | Rust file(s) | Test gate | Status |
|---|---|---|---|---|
| T9.1 | CLI global settings and parsed invocation contract (`--data-dir`, `--backend`, `--dsn`, split-derived, persistence mode) | src/cli.rs, tests/cli.rs | Rust tests accept valid flags, reject invalid backend/persistence, infer env defaults | done |
| T9.2 | CLI command routing for demo, ingest, report, workbench, mcp, serve, embedding_service, seed_bundle | src/cli.rs | Rust test routes each command and preserves required/optional arguments | done |
| T9.3 | Archive, embeddings migration, codex memory/bridge, compose command routing | src/cli.rs | Rust tests cover all subcommands and handler identities | done |
| T9.4 | Daemon projection/maintenance and maintenance-control routing | src/cli.rs | Rust tests route lifecycle subcommands and payload fields | done |
| T9.5 | CLI dispatch/runtime seam into existing Rust port services | src/cli.rs, src/cli_runtime.rs | Rust tests execute at least demo/report happy paths through Rust runtime | done |
| T9.6 | MCP tool registry parity with Python `_tool_specs()` | src/mcp_server.rs, src/mcp_tool_specs_1.json, src/mcp_tool_specs_2.json, src/mcp_tool_specs_3.json, src/mcp_tool_specs_4.json | Rust tests compare tool names, descriptions, schemas, and annotations | done |
| T9.7 | MCP call dispatch and JSON-RPC error contracts | src/mcp_server.rs | Rust tests cover list/call, unknown tool, payload decoding, success result shape | done |
| T9.8 | MCP auth settings, header/identity extraction, request-header propagation | src/mcp_auth.rs, src/mcp_server.rs | Rust tests cover env fallback, bearer identity, forbidden headers | in_progress |
| T9.9 | MCP stdio transport and streamable HTTP ASGI-equivalent contract | src/mcp_server.rs | Rust tests cover initialized request, notification handling, HTTP method/path dispatch | done |
| T9.10 | MCP SSE transport and session/event contract | src/mcp_server.rs | Rust tests cover session headers, event stream framing, disconnect behavior | done |
| T9.11 | MCP runtime gateway seam into existing Rust services | src/mcp_runtime.rs | Rust tests execute representative tool end-to-end without Python | done |
| T9.12 | Daemon/longrun/otel/projection-worker runtime support for CLI and MCP | src/cli_runtime.rs, src/daemon_loop.rs, src/otel.rs | Rust tests cover lifecycle startup, shutdown, trace/event export contract | in_progress |
| T9.13 | End-to-end CLI + MCP Rust smoke tests and CI gate | tests/cli.rs, tests/mcp_server.rs | cargo fmt/clippy/test green; no Python invoked in Rust pathway | planned |
