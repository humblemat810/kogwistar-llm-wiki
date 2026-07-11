# Long-Run Workflow Test

## Fair Maintenance-First Slices

The `Longrun: PgVector Fair Maintenance First (3 docs)` launch profile runs
`maintenance_first` with cooperative maintenance slices. Each document first
persists its authoritative source seed and maintenance request. The worker then
executes at most the configured slice budget, checkpoints the runtime frontier,
and requeues unfinished work at the durable queue tail before the harness moves
to the next document.

The VS Code prompts control:

- `KOGWISTAR_LONGRUN_MAINTENANCE_STEPS_PER_SLICE`
- `KOGWISTAR_LONGRUN_MAINTENANCE_LLM_CALLS_PER_SLICE`
- `KOGWISTAR_LONGRUN_MAINTENANCE_SECONDS_PER_SLICE`

Successful jobs are marked `DONE`; suspended jobs retain their continuation
run, node, and token IDs in the queue payload. A later claim resumes that
checkpoint. The source graph and usage events are persisted before the job is
returned to the queue. The fair queue primitive is implemented by Kogwistar's
job subsystem and is shared by SQLite, Postgres, and in-memory test stores.

The long-run workflow test is an opt-in diagnostic soak harness for
`kogwistar-llm-wiki`. It is intentionally not a production CLI command. The
test drives a generated corpus through the same runtime, ingestion, projection,
and maintenance primitives that normal app code uses, then writes a diagnostic
dump that can be shared with ChatGPT for post-run analysis.

`KOGWISTAR_LONGRUN_MAINTENANCE_WORKERS` controls how many maintenance workers
claim jobs during a poll. `1` is the safest sequential setting. Values greater
than `1` use the Kogwistar durable lease/claim queue and require PostgreSQL or
PgVector because Chroma is single-writer. This is execution parallelism, not
parser-worker configuration and is intentionally not part of the experiment
fingerprint.

See the workflow diagrams in [diagrams.md](./diagrams.md#long-run-workflow-test)
for the step flow and document-state view.

Known implementation review items are tracked in
[longrun_workflow_review_findings_checklist.md](./longrun_workflow_review_findings_checklist.md).

## Enable It

The test is skipped by default. Run it explicitly:

```powershell
$env:KOGWISTAR_LLM_WIKI_LONGRUN='1'
.\.venv\Scripts\python.exe -m pytest -m "longrun" tests/integration/test_longrun_workflow_ingestion.py -q -p no:cacheprovider
```

Backend selection is explicit too:

- `KOGWISTAR_LONGRUN_BACKEND=chroma` uses the local persistent Chroma-backed namespace engines.
- `KOGWISTAR_LONGRUN_BACKEND=postgres` uses the repo's Postgres + pgvector path and requires `KOGWISTAR_LONGRUN_DSN` or `KOGWISTAR_LLM_WIKI_TEST_PG_DSN`.
- `KOGWISTAR_LONGRUN_BACKEND=pgvector` is the explicit alias for the same Postgres + pgvector path. The probe launch now has two entries: one that self-provisions a disposable `testcontainers` Postgres+pgvector container, and one that asks for a custom DSN.
- `KOGWISTAR_LONGRUN_BACKEND=pgvector` also supports `KOGWISTAR_LONGRUN_PG_SOURCE=persistent`, which reuses one named dev container across runs and isolates experiments by database name inside that shared container.
- `in_memory` is not supported for the long-run soak because crash-continuation needs durable state.

For `pgvector`, the experiment launch configs now share one container per test session and create a fingerprinted database name for each semantic experiment. The fingerprint includes the workspace, backend, operation mode, corpus/profile, parser/provider/model, proposal mode, token bounds, and maintenance invariant. `parser_workers` is intentionally excluded so you can change parallelism without invalidating the experiment identity. Increasing only the runtime or LLM call budget also keeps the same experiment database identity, while changing the semantic inputs yields a different database name.

Resume compatibility is intentionally worker-agnostic too: a `continue` run can pick up a prior checkpoint even if the old manifest was created with a different parser worker count. That keeps parallelism tunable without forcing a fresh rebuild.

Database lifecycle is explicit:

- `fresh` + fingerprint mode on a managed `testcontainer` or `persistent` dev container drops and recreates only that fingerprinted database before the run. This is the development reset operation; it does not drop the whole PostgreSQL server or other experiment databases.
- `continue`, `auto`, and `retry_failed` preserve the database and reuse its durable artifacts.
- `shared` mode never resets automatically.
- `custom` DSNs never reset automatically, even for `fresh`; use an explicit database administration operation if you intentionally own that database.

The reset is performed by the pytest long-run harness, not by production `kogwistar` graph code. PostgreSQL still uses shared tables/schema inside each database; the database itself is the isolation boundary. Run IDs and workspace namespaces remain useful metadata, but they are not a substitute for database isolation.

If you prefer a VSCode button, use the launch configurations in
`.vscode/launch.json`:

- `Longrun: Chroma Fresh (20 docs)`
- `Longrun: Chroma Continue (20 docs)`
- `Longrun: Chroma Fresh (3 docs)`
- `Longrun: Chroma Continue (3 docs)`
- `Longrun: Chroma Fresh (1 doc)`
- `Longrun: Chroma Continue (1 doc)`
- `Longrun: Azure OpenAI Fresh (20 docs)`
- `Longrun: Azure OpenAI Continue (20 docs)`
- `Longrun: Azure OpenAI Auto Resume or Fresh (20 docs)`
- `Longrun: Postgres Fresh (20 docs)`
- `Longrun: Postgres Continue (20 docs)`
- `Longrun: Postgres Fresh (3 docs)`
- `Longrun: Postgres Continue (3 docs)`
- `Longrun: Postgres Fresh (1 doc)`
- `Longrun: Postgres Continue (1 doc)`
- `Probe: PgVector Fresh (1 doc, fingerprint DB)`
- `Probe: PgVector Custom DSN (1 doc, custom DSN)`
- `Probe: PgVector Persistent Dev Container (1 doc, fingerprint DB)`
- `Longrun: PgVector Fresh (20 docs, fingerprint DB)`
- `Longrun: PgVector Continue (20 docs, fingerprint DB)`
- `Longrun: PgVector Auto Resume or Fresh (20 docs, fingerprint DB)`
- `Longrun: PgVector Persistent Fresh (20 docs, fingerprint DB)`
- `Longrun: PgVector Persistent Continue (20 docs, fingerprint DB)`
- `Longrun: PgVector Persistent Auto Resume or Fresh (20 docs, fingerprint DB)`
- `Longrun: PgVector Azure OpenAI Fresh (20 docs, fingerprint DB)`
- `Longrun: PgVector Azure OpenAI Continue (20 docs, fingerprint DB)`
- `Longrun: PgVector Azure OpenAI Retry Failed (fingerprint DB)`
- `Longrun: PgVector Azure OpenAI Auto Resume or Fresh (20 docs, fingerprint DB)`

The fresh configurations reset the fingerprinted dev database and clear the
run directory first via harness mode, and the continue configurations reuse
the same stable run directory and database. Use separate
run directories per backend so chroma and postgres runs do not share checkpoint
state.

The pgvector probe self-provisions a disposable `testcontainers` database in
the `Probe: PgVector Fresh (1 doc, fingerprint DB)` launch entry. The
`Probe: PgVector Custom DSN (1 doc, custom DSN)` launch entry uses the DSN you
type into the prompt, which is useful when you want to point at a particular
external database.

The persistent dev-container launch entries use a named local Docker container
and leave it running after the test exits. That is the intended mode when you
want to inspect data later or compare multiple fingerprinted experiments in the
same container with different database names. The defaults are:

- container name: `kogwistar-llm-wiki-pgvector-dev`
- host port: `35432`
- internal Postgres port: `5432`

Continuation still happens by stable run directory plus stable fingerprinted
database name. The persistent container only makes the backing service durable;
the harness still isolates experiments by database name rather than by schema.

Parser-level continuation is separate from the outer long-run document attempt:

- Each parser child uses a stable inner workflow run ID,
  `parser:<source-document-id>`.
- The parser engine directory and workflow checkpoints remain under
  `parser_runs/<doc-id>`, so a timeout does not create an unrelated inner run.
- A timeout or child failure marks the next parser attempt for checkpoint
  resume. If no inner checkpoint exists, the client safely falls back to a
  fresh execution with the same stable ID.
- Provider usage events are appended and flushed to `usage_events.jsonl` after
  each callback. Recovery imports those events before returning failure, and
  stable event IDs make repeated imports idempotent.
- Ctrl+C while the parent is waiting for a parser child terminates that child,
  writes an interrupted heartbeat/manifest snapshot, and marks the document
  for parser resume. Use the `Retry Failed` launch configuration for the next
  invocation; `continue` intentionally skips terminal failures.

Completed parser steps are not replayed; only an in-flight provider call may be
retried. The outer manifest remains the source of document-attempt status,
while Kogwistar workflow checkpoints remain the source of inner parser
progress.

Operator guidance:

- `1 doc` and `3 docs` are diagnostic probes for checking parse, persistence,
  maintenance, and projection behavior quickly.
- `20 docs` is the full acceptance soak and is the one that should be used
  before treating the harness as healthy for the long-run contract.
- The continue launch configs reload prior dump history and resume suspended
  document workflows through `WorkflowRuntime.resume_run(...)` when the
  manifest contains suspended token metadata.

Defaults:

- `KOGWISTAR_OLLAMA_MODEL=gemma4:e2b`
- `KOGWISTAR_OLLAMA_BASE_URL=http://localhost:11434`
- `KOGWISTAR_LONGRUN_DOC_COUNT=20`
- `KOGWISTAR_LONGRUN_CORPUS_PROFILE=daily_life|watershed_stress` selects the
  generated corpus shape. `daily_life` is the normal-usage corpus with varied
  household, planning, and personal-knowledge notes. `watershed_stress` is the
  harder, more repetitive stress corpus.
- `KOGWISTAR_LONGRUN_BACKEND=chroma|postgres|pgvector`
- `KOGWISTAR_LONGRUN_PARSER_PROVIDER=ollama|azure_openai|openai|gemini`
- `KOGWISTAR_LONGRUN_PARSER_MODEL` selects the model for real LLM parsing.
- `KOGWISTAR_LONGRUN_DSN` is only needed when `KOGWISTAR_LONGRUN_BACKEND=postgres|pgvector` and no testcontainer-provided DSN is present
- `KOGWISTAR_LONGRUN_PG_SOURCE=testcontainer|custom|persistent` selects whether pgvector uses a disposable testcontainer, an explicit DSN, or a reusable named dev container
- `KOGWISTAR_LONGRUN_PERSISTENT_PG_CONTAINER_NAME` defaults to `kogwistar-llm-wiki-pgvector-dev`
- `KOGWISTAR_LONGRUN_PERSISTENT_PG_PORT` defaults to `35432`
- `KOGWISTAR_LONGRUN_MAX_REPEATED_SYSTEMIC_ERRORS=3`
- `KOGWISTAR_LONGRUN_MAX_POST_DOC_MAINTENANCE_STEPS=100`
- `KOGWISTAR_LONGRUN_MAX_RUNTIME_SECONDS=1200` bounds the overall run wall-clock
  time for bounded continue probes.
- `KOGWISTAR_LONGRUN_MAX_LLM_CALLS=100` adds an explicit call budget that is
  recorded in the dump and stops the run once exceeded. The recorded call
  count is based on provider callback invocations, including proposal, review,
  refinement, and correction calls, not merely one count per document.
- `KOGWISTAR_LONGRUN_PARSER_INPUT_COST_PER_1K_TOKENS` and
  `KOGWISTAR_LONGRUN_PARSER_OUTPUT_COST_PER_1K_TOKENS` optionally provide an
  explicit USD rate card; `KOGWISTAR_LONGRUN_PARSER_CACHED_INPUT_COST_PER_1K_TOKENS`
  optionally prices cached input separately. For known GPT-5 mini/nano models,
  a labelled public reference card is used when overrides are absent; Azure
  values remain estimates because deployment pricing can differ.
  explicit USD rate card for providers such as Azure that return token usage
  but not monetary cost. Cost reports then include `cost_status=estimated` and
  the configured rate-card source. Without these settings, missing provider
  cost is reported as `total_cost=null` and `cost_status=unavailable`, never as
  a measured zero.
- `KOGWISTAR_LONGRUN_PARSER_WORKERS=1|N` bounds concurrent document workflows.
  `1` preserves the legacy sequential behavior. Values above `1` require the
  Postgres/pgvector backend because Chroma is treated as single-writer. The
  maintenance worker remains one foreground worker; completed parser futures
  are persisted and maintenance jobs are polled by the parent in completion
  order. Resume-probe suspension is a thread-safe, one-shot run-level gate,
  so a parallel parser run may suspend one document and continue later with a
  different worker count. The harness also exposes
  `await harness.run_async()` using the same durable scheduler and terminal
  status path.
- `KOGWISTAR_LONGRUN_DOC_LIMIT=0|N` limits the number of eligible documents
  processed in this invocation. `0` means all; on `continue`, terminal
  documents are skipped and `N` counts additional eligible documents.
- `KOGWISTAR_LONGRUN_MODE=retry_failed` loads an existing checkpoint and
  reopens only `FAILED` or `QUARANTINED` documents. Completed documents and
  prior failure records are preserved. It never creates a fresh corpus.
- `KOGWISTAR_LONGRUN_RECOVERY_DOC_LIMIT=0|N` limits how many terminal failure
  documents are reopened in one recovery invocation. `0` means all eligible
  failures.
- `KOGWISTAR_LONGRUN_RECOVERY_ATTEMPTS_PER_DOC=N` limits cumulative recovery
  attempts for each document across recovery invocations. An attempt is
  consumed when the document reaches `claim_document`, not merely when it is
  selected or reopened. Recovery selections are persisted, so an interrupted
  recovery does not consume an attempt and does not broaden into untouched
  pending documents. A document that reaches the limit remains terminal until
  an operator deliberately starts a fresh experiment.
- Budget stops still fail the active pytest invocation so an incomplete
  acceptance run is visible, but untouched documents remain `PENDING` for a
  later `continue` or `auto` run instead of being quarantined.
- `KOGWISTAR_LONGRUN_OPERATION_MODE=parse_first|maintenance_first|hybrid`
  controls whether the long-run ingest request uses the normal parse-first
  path, the maintenance-first seed-only path, or the hybrid path.
- `KOGWISTAR_LONGRUN_PARSER_PROPOSAL_MODE=children|boundaries` controls the
  workflow-layered proposal strategy. It is part of the parser experiment
  fingerprint because it can change tree shape, fallback rate, and graph
  quality.
- `KOGWISTAR_LONGRUN_RUN_DIR` can point the harness at a stable run directory
  for crash-continuation probes and repeated manual reruns.
- `KOGWISTAR_LONGRUN_MODE=fresh|continue|auto|retry_failed` selects whether
  the harness wipes the run directory first, resumes an existing checkpoint
  only, tries to reuse a matching checkpoint and otherwise falls back to
  fresh, or performs bounded recovery of terminal failures.
- `KOGWISTAR_LONGRUN_DOC_COUNT=1|3|20` is supported for the VSCode launch
  buttons. Smaller corpora require `KOGWISTAR_LONGRUN_ALLOW_SMALL=1`.
- The VSCode long-run buttons include a corpus profile picker. The corpus
  profile is part of the experiment fingerprint, so `daily_life` and
  `watershed_stress` use separate checkpoints and fingerprinted pgvector
  databases even when the backend, parser, and operation mode are unchanged.

If the long-run flag is set and Ollama is unavailable, the test fails with a
minimal dump instead of silently skipping.

## Folder Contract

Each run creates:

- `input/`: generated documents not yet claimed
- `processing/`: documents claimed by this run
- `completed/`: document-level ingest, parse, persistence, and artifact checks succeeded
- `failed/`: document-specific failures after classification
- `quarantine/`: systemic or suspicious failures, including active documents during abort
- `dump/`: diagnostic package written after run start on success or abort

Every generated document starts in `input/` and ends in `completed/`, `failed/`,
or `quarantine/`.

## Runtime Loop

The harness runs like a single bounded daemon:

1. Generate a coherent 20+ document corpus.
2. Execute one runtime workflow per document.
3. Poll background maintenance opportunistically.
4. Continue until all documents are terminal.
5. Drain maintenance for up to 100 post-document steps or until the queue is quiet.
6. Run projection/read checks and graph invariants.
7. Finalize the dump and optional zip.

If `KOGWISTAR_LONGRUN_RUN_DIR` is set, the harness reuses that run directory
and loads the latest manifest checkpoint from `dump/manifest.jsonl` before it
starts. That lets a repeated invocation compare progress against the previous
run instead of treating every rerun as a fresh corpus. `continue` resumes only
when a checkpoint is already present, while `auto` reuses a matching checkpoint
and otherwise starts from scratch. Continue and auto reruns also reload
`status_transitions.jsonl` and `failure_records.jsonl` so the dump keeps earlier
transition and failure history.

The dump records a `corpus_fingerprint` derived from the selected corpus mode,
corpus profile, operation mode, parser lane, backend, and profile settings.
Checkpoint reuse only happens when the fingerprint matches, which keeps
distinct corpus and operation modes from crossing over into each other.

When `KOGWISTAR_LONGRUN_RESUME_PROBE=1`, the document workflow deliberately
suspends after parsed graph persistence and before background maintenance. In
parallel mode, exactly one document claims this run-level gate; other workers
remain ordinary pending work and are cancelled or checkpointed by the parent.
The
dump records the runtime checkpoint step, suspended node id, and suspended token
id. A later continue run uses those values with `WorkflowRuntime.resume_run(...)`
so the same document workflow continues past the suspension point instead of
starting from a fresh initial state. If a document is marked `SUSPENDED` without
that token metadata, continue mode fails loudly.

If `KOGWISTAR_LONGRUN_MODE` is:

- `fresh`, the harness clears the run directory before starting.
- `continue`, the harness requires a compatible checkpoint manifest and fails
  if one is not available.
- `auto`, the harness continues when the checkpoint matches the requested
  document count and otherwise starts fresh.

The workflow stages are:

```text
claim_document
token_check
parse_document
persist_document
enqueue_background_maintenance
observe_background_maintenance
verify_document_artifacts
move_completed
```

## Failure Semantics

Primary parse failures are document-scoped after retries. LLM quality failures
from maintenance or derived artifacts are recoverable and do not abort the run.

Recoverable document failures include:

- `token_count_out_of_range`
- `document_parse_failed`
- `document_persist_failed_after_retries`
- `maintenance_artifact_missing_for_doc`

Recoverable LLM quality failures include:

- `llm_invalid_json`
- `llm_unsupported_citation`
- `llm_ungrounded_output`
- `llm_contradicts_source`
- `llm_empty_or_low_confidence_output`

Systemic abort-class failures include:

- `database_write_repeated_failure`
- `graph_invariant_violation`
- `projection_repair_failure`
- `runtime_worker_stuck`
- `ollama_unavailable_repeatedly`
- `same_error_repeated_across_unrelated_docs`

The circuit breaker normalizes systemic error fingerprints and aborts only when
the same infrastructure-shaped error repeats across unrelated documents. LLM
hallucination or grounding failures do not count toward systemic abort unless
they indicate the model service itself is unavailable or unusable for every
document.

## Diagnostic Dump

The dump includes:

- `run_config.json`
- `manifest.jsonl`
- `status_transitions.jsonl`
- `failure_records.jsonl`
- `error_fingerprints.json`
- `folder_inventory.json`
- `progress_summary.json`
- `recovery_summary.json`
- `graph_export.json`
- `promotion_evidence_pack` records for promoted documents
- `projection_summary.json`
- `maintenance_summary.json` with maintenance job ids, source document ids,
  and maintenance-specific step counts
- `llm_calls_summary.json` with parser/provider metadata, `call_count`, and
  the configured call/runtime budgets
- `parser_layer_logs/<doc-id>.json` with per-document parser-layer trace entries
  for workflow-layered runs; `parser_layer_log.json` remains as the legacy
  `doc-001` inspection artifact
- `sampled_prompts_and_responses.jsonl`
- `raw_documents/`
- `final_report.md`
- `longrun-dump.zip` beside the `dump/` directory on finalization

On abort, the harness writes an abort snapshot, moves active processing
documents to `quarantine/`, and then finalizes the dump so the report matches
the final folder state.

For the longer architectural explanation of why the harness exists, see
[diagrams.md](./diagrams.md#long-run-workflow-test) and
[testing_guide.md](./testing_guide.md#long-run-workflow-test).

# Experiment Isolation

Long-run artifacts are fingerprint-scoped. The VS Code launch configurations
prompt for an optional `experiment_run` label. Reuse the same label to continue
the same experiment; change it for a fresh repeat such as `mini-2`. The label
is included in the corpus fingerprint and therefore selects a separate
fingerprinted PostgreSQL namespace and dump directory. Parser worker count is
not included in the fingerprint, so a continuation may use a different worker
count.

If an older fixed run directory already contains a different fingerprint, the
harness places the new experiment under `experiments/<fingerprint>` and never
resets the existing directory.
