# Rust Port

This workspace is the Rust port for the root `kogwistar-llm-wiki` repository.
It is intentionally separate from the vendored `kogwistar/` checkout and the
historical `experiments/dual_language_seam_poc` proof of concept.

## Current surface

- `kogwistar-llm-wiki-port::archiving` mirrors archive format/secret guards,
  safe artifact paths, restore reports, and embedding fingerprints.
- `kogwistar-llm-wiki-port::configuration::workspace` mirrors the app-level
  graph-space enum, workspace namespace helpers, namespace metadata, and
  workspace/graph-space metadata matcher from
  `src/kogwistar_llm_wiki/configuration/workspace.py`.
- `kogwistar-llm-wiki-port::compose` mirrors typed deployment option validation
  and dependency-free Compose text checks.
- `kogwistar-llm-wiki-port::models` mirrors the ingest, maintenance,
  Obsidian-build, and projection snapshot contract models from
  `src/kogwistar_llm_wiki/models.py`, including Python wire names and defaults.
- `kogwistar-llm-wiki-port::projections` mirrors the bounded projection
  snapshot helpers from `src/kogwistar_llm_wiki/projections/snapshot.py`:
  graph-space normalization, supported graph-space namespaces, manifest-based
  node visibility, and bidirectional adjacency construction.
- `kogwistar-llm-wiki-port::ingest::source_lifecycle` mirrors immutable source
  IDs, revisions, readiness IDs, operation-mode classification, and durable
  parse-limit validation from the root ingest source lifecycle.
- `kogwistar-llm-wiki-port::ingest::artifacts` mirrors leading artifact spans
  and immutable revision-document selection used by ingest artifacts.
  It also mirrors artifact-node metadata and lifecycle defaults.
- `kogwistar-llm-wiki-port::ingest::source_parsing` mirrors parser provider
  mode normalization and environment-backed model priority.
- `kogwistar-llm-wiki-port::ingest::maintenance_requests` mirrors deterministic
  maintenance request fingerprints, request/lane stable IDs, candidate-link and
  promotion-candidate IDs, and promotion evidence-pack digests.
  It also mirrors promoted-knowledge IDs and promotion decision metadata.
- `kogwistar-llm-wiki-port::parsing` mirrors immutable parse regions/targets and
  parse session, generation, member, frontier, and legacy generation IDs.
- `kogwistar-llm-wiki-port::provenance` mirrors evidence-pack digest
  canonicalization and deterministic SHA-256 hashing.
- `kogwistar-llm-wiki-port::seeding` mirrors graph-seed source grounding,
  endpoint validation, reserved-property guards, and canonicalization.
- `kogwistar-llm-wiki-port::policies` mirrors conservative promotion decisions,
  artifact taxonomy visibility, and projection eligibility.
  It also mirrors derived-knowledge and execution-wisdom query/metadata contracts
  plus provenance-required lifecycle classification.
- `kogwistar-llm-wiki-port::providers` mirrors provider aliases, OpenAI model
  env suffixes, parser endpoint defaults, and environment resolution.
  It also mirrors parser proposal-mode settings, provider summaries, and
  opt-in maintenance provider chains.
- `kogwistar-llm-wiki-port::longrun` mirrors long-run parser payload path/int
  validation, object mapping, and proposal-mode summaries.
  It also mirrors child paths, live-trace flags, heartbeat envelopes, and
  workflow-status result classification.
  It also mirrors child result and failure envelopes plus provider-run call
  counting.
- `kogwistar-llm-wiki-port::layered` mirrors layered-parse persistence modes,
  run-ID fallbacks, layer-log events, engine build options, and typed result
  workflow status.
- `kogwistar-llm-wiki-port::usage` mirrors budget-event envelopes, attribution,
  provider call counts, token/cost summaries, and cost-status fallbacks.
  It also mirrors state-backed token/time/rate/step/call/cost budget ledgers
  with trusted ceilings and suspension checks.
  It also mirrors generic usage adapters, stable event IDs, provider callback
  ingestion, cost estimates, missing-cost labels, and provider errors.
  It also mirrors token-pricing overrides/reference cards, provider usage
  extraction, and labelled cost estimation.
  It also mirrors parser proposal-mode settings, provider summaries, and
  opt-in maintenance provider chains.
- `kogwistar-llm-wiki-port::maintenance` mirrors the durable document
  maintenance plan and one-phase continuation planner.
- `kogwistar-llm-wiki-port::maintenance_queue` mirrors maintenance failure
  retry/fail decisions, bounded selection payloads, suspended frontier
  requeue payloads, persisted budget-state filtering, and lifecycle traces.
- `kogwistar-llm-wiki-port::maintenance_lease` mirrors lease renewal outcomes,
  claim fencing, stale-work rejection, and maintenance reply idempotency keys.
- `kogwistar-llm-wiki-port::maintenance_lane` mirrors lane-reply normalization
  and suppression, durable authority snapshots/identity, and runtime authority
  context.
- `kogwistar-llm-wiki-port::maintenance_worker` mirrors queued revision fences
  and stable maintenance-run identity contracts.
- `kogwistar-llm-wiki-port::ingest::queue_payloads` mirrors bounded
  maintenance-context guards and maintenance-job queue payload construction.
- `kogwistar-llm-wiki-port::ingest::run` mirrors ingest operation branches,
  durable-session selection, artifacts, runtime timing, and trace envelopes.
- `kogwistar-llm-wiki-port::diagnostics` mirrors JSONL trace append, stage
  timing summaries/aggregation, and semantic-tree quality counts.
- `kogwistar-llm-wiki-port::parse_quality` mirrors basic-sense graph evaluation,
  span coverage, duplicate excerpts, fallback penalties, and verdicts.
- `kogwistar-llm-wiki-port::parse_statistics` mirrors durable parse-run record
  fields, graph/tree counts, optional quality fields, and canonical details JSON.
- `kogwistar-llm-wiki-port::ingest::maintenance_requests` also mirrors
  projection-request IDs and queue payloads.
- `kogwistar-llm-wiki-port::ids` mirrors the Python stable-ID contract,
  including non-ASCII canonical JSON behavior.

## Commands

From this directory:

```powershell
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

## Python facade

`rust/crates/kogwistar-llm-wiki-python` builds a root-native `_kogwistar_llm_wiki_rust`
PyO3 extension. Its first JSON ABI mirrors stable IDs, canonical JSON,
evidence-pack hashing, and basic-sense evaluation.

`src/kogwistar_llm_wiki/native.py` is the stable loader: use
`KOGWISTAR_LLM_WIKI_RUST=native` to require the extension, `python` to force
fallback, or `auto` (default) to use the extension when present.

Build and install the current-host extension with:

```powershell
python scripts/build_native.py
```

The script maps Cargo's host artifact to Python's extension name and copies it
into `src/kogwistar_llm_wiki/`; setuptools includes both `.pyd` and `.so`
facade artifacts in package data.

The port is incremental. Prefer one Python contract slice at a time, keep the
wire names stable, and add a Rust test for each behavior before expanding
another module.
