# Python-to-Rust Contract Inventory

This inventory tracks Python contract surfaces that have Rust mirrors or verified
Rust-port gaps.  It does **not** claim that Python-only CLI, HTTP, process, or
provider orchestration has been mirrored.

## Contract groups

| Python surface | Rust module(s) | Status | Evidence |
|---|---|---|---|
| `app_contracts.messaging` | `app_contracts.rs` | implemented | `tests/app_contracts.rs` |
| `app_contracts.obsidian_source` | `app_contracts.rs` | implemented | `tests/obsidian_source.rs` |
| `backends.registry` | `backends.rs` | partial | `tests/backends_registry.rs` |
| `archiving.archive_contracts` | `archive_restore.rs`, `archiving.rs` | implemented | `tests/archiving.rs` |
| `configuration.workspace` | `configuration/workspace.rs` | implemented | `tests/workspace.rs` |
| `configuration.resource_authorizer` | `configuration/resource_authorizer.rs` | partial | `tests/resource_authorizer.rs`; explicit spec parsing and env selection are mirrored, Python dynamic import remains authoritative |
| `configuration.identity` | `configuration/identity.rs` | partial | `tests/configuration_identity.rs`; scope, workspace ACL, disabled bypass, and runtime authority context are mirrored; bearer/JWT I/O remains Python-only |
| `models` | `models.rs` | implemented | `tests/models.rs` |
| `projections.snapshot` | `projections.rs` | implemented | `tests/projections.rs` |
| `ingest.artifacts`, `ingest.source_lifecycle`, `ingest.source_parsing`, `ingest.maintenance_requests` | `ingest/*` | implemented | integration tests under `tests/` |
| `parsing.*` IDs and stores | `parsing.rs` | partial | durable-store I/O remains Python-only |
| `parsing.parse_quality`, `parsing.parse_statistics` | `parse_quality.rs`, `parse_statistics.rs` | implemented | `tests/parse_quality.rs`, `tests/parse_statistics.rs` |
| `parsing.parse_reconciliation` | `parse_reconciliation.rs` | implemented | `tests/parse_reconciliation.rs` |
| `parsing.layered_workflow`, `parsing.longrun_*` | `layered.rs`, `longrun.rs`, `parsing_longrun.rs` | implemented | `tests/layered.rs`, `tests/parsing_longrun.rs` |
| `maintenance.*` worker, queue, lease, patch, policy, and selection contracts | `maintenance_*.rs`, `patch_policy.rs`, `worker_*.rs` | partial | core contracts are tested; Python durable graph stores remain authoritative |
| `memory.service` | `project_memory.rs` | implemented | `tests/project_memory.rs` |
| `usage.*` | `usage.rs` | implemented | `tests/usage.rs` |
| `providers.*` | `providers.rs` | implemented | `tests/provider_config.rs` |
| `seeding.*` | `seeding.rs` | implemented | `tests/seeding.rs` |
| `codex.codex_bridge`, `codex.codex_workbench_agent`, `codex.codex_compose_tui` | `codex_bridge.rs`, `structured_runner.rs`, `compose_tui.rs` | implemented | integration tests under `tests/` |
| `daemon`, `daemons.*` | `daemon_loop.rs` | partial | loop bounds are mirrored; process supervision is Python-only |
| `workbench.*` | `lens_projection.rs`, `semantic_lens.rs`, `cockpit.rs`, `crosslink_*.rs` | partial | core projection and review contracts are tested; HTTP/API is Python-only |
| `agent.source_tools` | `source_unit.rs` | implemented | `tests/source_unit.rs` |
| `agent.gateway_protocol` | `gateway_protocol.rs` | implemented | `tests/gateway_protocol.rs`; job counts, answer extraction, A2A/task, JSON-RPC, and deterministic request IDs |
| `embeddings.*` | `embedding_adapter.rs`, `multimodal_projection.rs`, `grounding.rs` | partial | pure contracts are tested; remote/provider I/O is Python-only |
| `disambiguation.*`, `maintenance.reconciliation` | `disambiguation.rs`, `reconciliation.rs` | implemented | `tests/disambiguation.rs`, `tests/reconciliation.rs` |

## Coverage policy

`implemented` means the Python contract is mirrored in Rust and covered by a Rust
test.  `partial` means only the listed pure-contract subset is mirrored; Python
process I/O, durable stores, HTTP, CLI, or provider clients remain Python-only.
`not_applicable` applies only to Python-only orchestration edges and is not used
to hide a missing Rust contract.
