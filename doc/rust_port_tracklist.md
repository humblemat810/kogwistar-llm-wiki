# Root Rust Port Fine Tracklist

## Current Gate

- Command: `cargo fmt --all`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace` from `rust/`.
- Root-only scope: do not modify vendored `kogwistar/`, `kg-doc-parser/`, or adapter repositories.

## Done

### Contracts And Models

- [x] Stable IDs, canonical JSON, evidence-pack digests, and basic-sense evaluation.
- [x] Ingest, maintenance, Obsidian-build, and projection snapshot wire models.
- [x] Workspace and graph-space metadata contracts.

### Ingest And Parsing

- [x] Source lifecycle IDs and durable parse-limit validation.
- [x] Leading artifact spans and immutable revision-document selection.
- [x] Parser provider mode, model environment priority, and parser kwargs.
- [x] Maintenance request fingerprints, candidate IDs, promotion IDs, and evidence-pack digests.
- [x] Durable parse regions/targets and parse session IDs.
- [x] Ingest operation branches, durable-session selection, artifacts, runtime timing, and traces.
- [x] Bounded maintenance-context guards and maintenance-job queue payloads.
- [x] Long-run child paths, trace flags, heartbeat envelopes, result classification, and call counting.
- [x] Layered-parse persistence modes, run-ID fallback, layer-log events, and typed workflow status.
- [x] Parse-quality evaluation and durable parse-run statistics.

### Maintenance

- [x] Durable maintenance plan and one-phase continuation planner.
- [x] Failure retry/fail decisions, bounded selection payloads, suspended requeue payloads, and budget filtering.
- [x] Lease renewal, claim fencing, stale-work rejection, and reply idempotency keys.
- [x] Lane-reply normalization, suppression, durable authority snapshots/identity, and runtime authority context.
- [x] Queued revision fences and stable maintenance-run identity.
- [x] Source revision digests, readiness IDs, CAS guards, and parse-session supersession.
- [x] Bounded dependency invalidation planning and queue payload contracts.
- [x] Maintenance profile decisions, windowed budgets, and provider/profile fallback ladders.
- [x] Patch status and intent classification into document graph lifecycle states.
- [x] Maintenance-kind operation categories and append-only trace report aggregation.
- [x] Maintenance cost grouping by maintenance kind and model.
- [x] Connected, semantic, evidence/history candidate selection and seeded embedding probes.
- [x] Maintenance control state, deployment defaults, and fail-closed corruption policy.
- [x] Maintenance-kind normalization and workflow routing.
- [x] Maintenance job lowering into typed review patches.
- [x] Bounded structured maintenance continuation context and round append helpers.
- [x] Workspace metadata guards, semantic fingerprints, edge endpoints, and budget-state helpers.
- [x] Request-scoped seed collection, edge visibility, topic probes, and selection audit envelopes.
- [x] Bounded cross-link proposal, critic, review, and atomic-replacement contracts.
- [x] Deterministic graph context packing and optional provider budget limits.
- [x] Durable cross-link review batches, CAS outcomes, decision metadata, and revalidation payloads.
- [x] Bounded observation critic output validation and provider failure classification.
- [x] Compact observation records, entity metadata flattening, and span extraction.
- [x] Bounded read-only observation frames and deterministic quality assessments.
- [x] Durable workflow design artifacts for maintenance, cross-link, and multimodal retrieval.
- [x] Budget terminalization, bounded selection replies, and acknowledgement trace contracts.

### Projection, Policy, And Usage

- [x] Graph-space normalization, projection namespaces, node visibility, and adjacency helpers.
- [x] Conservative promotion policy and artifact taxonomy/visibility/lifecycle classification.
- [x] Budget events, budget ledgers, provider usage, callbacks, pricing, cost estimation, and summaries.

### Cross-Cutting Domains

- [x] Diagnostics JSONL and stage-timing aggregation.
- [x] Provider aliases, endpoint defaults, and maintenance provider chains.
- [x] Archive format/secret guards, safe artifact paths, restore reports, and embedding fingerprints.
- [x] Typed Compose option validation and dependency-free Compose text checks.
- [x] Graph-seed source grounding, endpoint validation, reserved-property guards, and canonicalization.

## Next

### Immediate Contract Slices

- [ ] Root native PyO3 facade ABI expansion beyond the first JSON seam.
- [ ] Maintenance worker graph-patch strategy inputs and accepted-candidate replacement contracts.
- [ ] Recovery isolation: checkpoint fallback, service-health repair actions, and workspace-filtered run-history contracts.
- [ ] Workbench: bounded lens projections, semantic-lens snapshots, cockpit action/patch proposal validation, and review artifacts.

### Larger Parity Slices

- [ ] Embedding adapters, runtime contracts, multimodal projections, grounding, and source-unit handling.
- [ ] Archive restore operations and event-chain validation beyond safety/report contracts.
- [ ] Disambiguation artifacts, service operations, reconciliation, and patch policy.
- [ ] Codex bridge, structured runner, compose TUI, and project-memory APIs.
- [ ] Daemon/projection/maintenance lifecycle loops and shared service-health/startup-recovery/budget/profile-ladder plumbing.

## Verification Rule

- Every Rust surface must be traceable to the Python contract it mirrors.
- Add Rust tests that preserve Python wire names, defaults, ordering, and failure categories.
- Before promoting a capability, compare Python and Rust normalized outputs on fixed inputs.
