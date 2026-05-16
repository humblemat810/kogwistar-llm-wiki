# Long-Run Workflow Test

The long-run workflow test is an opt-in diagnostic soak harness for
`kogwistar-llm-wiki`. It is intentionally not a production CLI command. The
test drives a generated corpus through the same runtime, ingestion, projection,
and maintenance primitives that normal app code uses, then writes a diagnostic
dump that can be shared with ChatGPT for post-run analysis.

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
- `KOGWISTAR_LONGRUN_BACKEND=postgres` uses the repo's disposable Postgres test-container path and requires `KOGWISTAR_LONGRUN_DSN` or `KOGWISTAR_LLM_WIKI_TEST_PG_DSN`.
- `in_memory` is not supported for the long-run soak because crash-continuation needs durable state.

If you prefer a VSCode button, use the launch configurations in
`.vscode/launch.json`:

- `Longrun: Chroma Fresh (20 docs)`
- `Longrun: Chroma Continue (20 docs)`
- `Longrun: Chroma Fresh (3 docs)`
- `Longrun: Chroma Continue (3 docs)`
- `Longrun: Chroma Fresh (1 doc)`
- `Longrun: Chroma Continue (1 doc)`
- `Longrun: Postgres Fresh (20 docs)`
- `Longrun: Postgres Continue (20 docs)`
- `Longrun: Postgres Fresh (3 docs)`
- `Longrun: Postgres Continue (3 docs)`
- `Longrun: Postgres Fresh (1 doc)`
- `Longrun: Postgres Continue (1 doc)`

The fresh configurations clear the run directory first via harness mode, and
the continue configurations reuse the same stable run directory. Use separate
run directories per backend so chroma and postgres runs do not share checkpoint
state.

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
- `KOGWISTAR_LONGRUN_BACKEND=chroma|postgres`
- `KOGWISTAR_LONGRUN_DSN` is only needed when `KOGWISTAR_LONGRUN_BACKEND=postgres`
- `KOGWISTAR_LONGRUN_MAX_REPEATED_SYSTEMIC_ERRORS=3`
- `KOGWISTAR_LONGRUN_MAX_POST_DOC_MAINTENANCE_STEPS=100`
- `KOGWISTAR_LONGRUN_RUN_DIR` can point the harness at a stable run directory
  for crash-continuation probes and repeated manual reruns.
- `KOGWISTAR_LONGRUN_MODE=fresh|continue|auto` selects whether the harness
  wipes the run directory first, reuses an existing checkpoint, or tries to
  reuse a matching checkpoint and otherwise falls back to fresh.
- `KOGWISTAR_LONGRUN_DOC_COUNT=1|3|20` is supported for the VSCode launch
  buttons. Smaller corpora require `KOGWISTAR_LONGRUN_ALLOW_SMALL=1`.

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
run instead of treating every rerun as a fresh corpus. Continue and auto reruns
also reload `status_transitions.jsonl` and `failure_records.jsonl` so the dump
keeps earlier transition and failure history.

When `KOGWISTAR_LONGRUN_RESUME_PROBE=1`, the document workflow deliberately
suspends after parsed graph persistence and before background maintenance. The
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
- `llm_calls_summary.json`
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
