# Kogwistar Rust Port: Pre-Migration Baseline and Evidence Plan

**Status:** Required before implementing or enabling a Rust-backed core  
**Prepared:** 2026-07-14  
**Companion policy:** [Consumer Compatibility Reminder](kogwistar_rust_port_compatibility_reminder.md)

## Purpose

This is the factual baseline and evidence plan to capture while the Python
implementation is still the reference behavior. It applies to
kogwistar-llm-wiki, kg-doc-parser, and kogwistar-obsidian-sink.

It is not a Rust implementation design. Its purpose is to make behavioral
drift visible before it reaches graph data, workflow state, or an Obsidian
vault.

## Current Source Revisions

These are the revisions present when this document was prepared. They identify
the source inspected, not a released or clean baseline. Before fixture capture,
record an immutable, clean migration tag for every repository.

| Repository | Branch | HEAD | Role |
| --- | --- | --- | --- |
| kogwistar-llm-wiki | feat/boundary-mode-upgrade | 30d682e | Product composition and acceptance harness |
| kogwistar | main | 206b43f | Current Python reference implementation |
| kg-doc-parser | feat/page-index-style-local-opt | a06f1cb | Parser/runtime consumer |
| kogwistar-obsidian-sink | main | c295894 | Projection/CDC consumer |

The final baseline record must also capture:

- full commit SHA and immutable release/migration tag for every repository;
- Python version, OS, architecture, lockfile resolution, and optional
  dependencies;
- backend configuration, embedding function identity, and relevant feature
  flags;
- fixture-generator and normalized-output format versions;
- capture time and whether data was fully synthetic.

Never commit credentials, customer sources, or an unrestricted production data
export. Use deterministic synthetic fixtures for the committed contract suite;
keep any operational evidence in its approved access-controlled store.

## Observed Consumer Contracts

### kogwistar-llm-wiki

The product directly composes:

- GraphKnowledgeEngine, its read/write subsystem shape, and namespace scoping;
- in-memory, Chroma persistent, and Postgres/pgvector engine construction;
- Document, GraphExtractionWithIDs, Node, Edge, Grounding, and Span validation
  and serialization;
- stable_id, logical references, provenance/evidence packs, and source-pointer
  validation;
- workflow runtime/results/resolvers, retries, checkpoints, durable jobs,
  recovery, and budget/usage attribution;
- core policy and maintenance helpers.

The app owns workspace and graph-space behavior. The port must preserve source,
base-KG, curated-KG, workflow, wisdom, and derived-knowledge routing rather
than collapsing it to a global graph.

### kg-doc-parser

The parser uses:

- Node, Edge, Grounding, and Span graph-ready payloads;
- WorkflowRuntime, StepContext, workflow graph/run models, and RunSuccess,
  RunSuspended, and RunFailure states;
- retry_with_context and RetryResult;
- stable_id, source-pointer validation, and fuzzy-offset matching.

The parser still owns source-map fidelity, grounded extraction, pointer repair,
and semantic-tree behavior. The core port must not silently reinterpret or
relocate those parser semantics.

### kogwistar-obsidian-sink

The composed product reads scoped Node/Edge data, creates projection entities,
and sends them to the sink. Its adapter observes entity IDs, labels/titles,
types, summaries, metadata, mentions/spans, source/target IDs, relationships,
and bodies. They are user-visible once notes are rendered.

Its event consumer currently recognizes entity.upsert and supports
sequence-based incremental consumption. Stable note identity, link targets,
relationship multiplicity, duplicate-title handling, full rebuilds, and the
vault ledger are compatibility behavior, not merely presentation.

## Reference Fixtures Required Before the Port

Create deterministic, privacy-safe fixture inputs and normalized expected
outputs from the Python reference implementation before replacing it. Commit
fixture sources under tests/fixtures/rust_port_compat/. Put generated
diagnostics only in an ignored run directory or approved artifact store. Every
artifact needs a schema/version header.

| Fixture | Minimum input | Required normalized evidence |
| --- | --- | --- |
| model_roundtrip | Document; grounded nodes/edges; multiple spans; optional/default fields | Validation and dump results, defaults, stable IDs, and retained provenance/offsets |
| graph_lifecycle | Create, idempotent write, update/replace, tombstone, and node/edge/document reference queries | Entities, event sequence, replay result, and query result at every phase |
| scope_and_visibility | Two workspaces; source/base/curated graph spaces; intentional cross-workspace edge | Exact allowed IDs and explicit assertions for forbidden IDs |
| workflow_recovery | Completion, retry exhaustion, suspended checkpoint, restart/resume, and durable claim/redelivery | Run status, checkpoint frontier, job state, trace IDs, resume output, and budget attribution |
| parser_contract | Fixed source units with valid, repairable, and invalid offsets | Semantic/export payload, grounded graph payload, repair decision, structured failure, and child IDs |
| projection_snapshot | Curated nodes, relationships, duplicate titles, dangling target, update, and tombstone | Projection entities, event cursor behavior, ledger/manifest, stable relative file map, and content hashes |
| backend_smoke | graph_lifecycle for each supported backend | Normalized output, backend version, and an explicit unsupported-capability result where relevant |

### Normalization rules

- Sort map keys and independent collections by stable semantic keys.
- Retain event order, trace-step order, and user-visible rendering order.
- Remove only documented volatile fields, such as capture timestamps or random
  temporary paths.
- Never normalize away IDs, namespaces, source offsets, event cursors,
  tombstones, error/result classifications, or vault-relative paths.
- Version the normalizer and fail on unknown fields rather than discarding them.

Python output is the initial oracle. A planned difference needs a versioned
fixture change, a compatibility-manifest decision, consumer approval, and a
release note before Rust output may replace that oracle.

## Backend and Data Safety Baseline

| Surface | Current operational meaning | Mandatory evidence/rule |
| --- | --- | --- |
| In-memory backend | Fast deterministic test/development path; not durable enough for crash-continuation soak | Run model, lifecycle, scope, parser, and projection fixtures here first. It does not prove persistent-store compatibility. |
| Chroma-backed persistent engines | Local persistent path with single-writer operating assumptions | Take a read-only fixture snapshot. Never attach Python and Rust implementations concurrently. No implicit write, index rebuild, compaction, or migration at Rust startup. |
| Postgres/pgvector | Durable multi-worker path used by the long-run harness | Capture graph, queue/checkpoint, replay, and recovery on an isolated database. Migration must be explicit, resumable, backed up, and rollback-tested. |
| Engine SQLite/meta state | Metadata, queue, run, and service/recovery support surface | Inventory touched tables/files, version schema changes, and prove that Python rollback can read canary state. |
| Events and projection ledger | CDC sequence/cursor plus vault materialization state | Preserve idempotency and ordering. Replay must converge without duplicate notes or ledger corruption. |

Before a persistent canary, take a verified backup and record the restore
procedure. The first Rust-backed write must be opt-in. A failed canary must
return to the Python facade using the same data, without manual repair,
duplicated events, or a forced vault rebuild.

## Required Test Evidence

Run these focused deterministic suites against the Python reference, save the
normalized evidence, then run them through the Rust-backed facade. Use the
configured repo-local cache or -p no:cacheprovider when cache permissions are
suspect; see [testing_guide.md](testing_guide.md).

### llm-wiki focused gate

    .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/unit/test_graph_space_query.py tests/unit/test_projection_consistency.py tests/unit/test_daemon_interrupt_recovery.py tests/unit/test_worker_runtime_orchestration.py tests/integration/test_ingest_pipeline_e2e.py tests/integration/test_obsidian_sink_end_to_end.py tests/integration/test_obsidian_vault_on_disk.py

### Parser focused gate

    .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider kg-doc-parser/tests/test_packaging_imports.py kg-doc-parser/tests/test_fuzzy_offsets_compat.py kg-doc-parser/tests/test_workflow_ingest_contracts.py kg-doc-parser/tests/test_workflow_ingest_conversation_graph.py kg-doc-parser/tests/test_workflow_ingest_resolver_invariants.py kg-doc-parser/tests/test_workflow_ingest_layerwise_llm.py

### Obsidian sink focused gate

    Push-Location kogwistar-obsidian-sink
    ..\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
    Pop-Location

The deterministic cross-repo fixture must cover source -> parser -> graph
ingest -> event/projection -> vault, then update, tombstone, event replay, and
full vault rebuild. It must run without a live LLM service.

The long-run harness is supplemental persistent/recovery evidence, not the
first parity test. Use its 1-document and 3-document probes during development
and the 20-document soak after deterministic fixtures pass. Chroma and
Postgres/pgvector runs need separate run roots; multi-worker maintenance needs
Postgres/pgvector because Chroma is single-writer. See
[longrun_workflow_test.md](longrun_workflow_test.md).

## Compatibility Manifest and Decision Log

Before implementation, create a machine-readable compatibility manifest owned
by kogwistar and checked from all three consumer suites. Every public contract
entry must record:

- Python import path and symbol, method, or protocol;
- consumers and their fixture/test coverage;
- contract version, status (preserved, adapter-backed, deprecated, or
  not-in-first-port), and core owner;
- Python behavior and Rust implementation route;
- serialization/event schema version and backend capability status;
- removal version and migration path for any deprecated contract.

Do not mark a contract complete with a broad Any or untyped dictionary. Use
explicit typed envelopes with an extension policy at dynamic boundaries.

The decision log must settle:

1. In-process binding or out-of-process service, plus failure, timeout,
   authentication, local-development, and version-negotiation behavior.
2. Port order for models, ID/provenance helpers, engine, runtime/jobs, events,
   and each backend.
3. Python-model ownership and the exact conversion/validation boundary.
4. Event cursor/sequence, ordering, idempotency, and tombstone behavior.
5. Storage read/write compatibility, migration, backup, and rollback per
   backend.
6. Feature flag/default selection and the Rust-default promotion criteria.
7. Native packaging targets, supported Python/OS versions, and the Python
   facade compatibility window.

## Ownership and Sign-off

| Evidence | Accountable role | Sign-off condition |
| --- | --- | --- |
| Core facade, model/engine/runtime/event parity | Kogwistar port owner | Python-facing manifest and core fixtures pass |
| App scope, promotion, recovery, orchestration | llm-wiki owner | Scoped ingest/recovery/projection evidence matches baseline |
| Grounding, source maps, offsets, parser workflow | Parser owner | Parser fixtures and focused suite match baseline |
| CDC, projection, ledger, deterministic notes/rebuilds | Obsidian sink owner | Incremental and full-rebuild vault output matches baseline |
| Storage migration, rollback, release coordination | Rust-port release owner | Backup/restore and Python rollback rehearsed on every changed store |

The port cannot advance from canary to default until every row has a named
owner, evidence location, and passing result. A green core-only test run, or an
empty-store demo, is not sign-off.

## First Implementation Milestone

The first milestone is complete only when one compatible Python facade can
select the current Python implementation or a Rust-backed implementation, and
both pass the model-roundtrip, graph-lifecycle, scope-and-visibility,
parser-contract, and projection-snapshot fixtures. Persistent writes, data
migration, and changing the default come later.
