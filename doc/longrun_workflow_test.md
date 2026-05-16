# Long-Run Workflow Test

The long-run workflow test is an opt-in diagnostic soak harness for
`kogwistar-llm-wiki`. It is intentionally not a production CLI command. The
test drives a generated corpus through the same runtime, ingestion, projection,
and maintenance primitives that normal app code uses, then writes a diagnostic
dump that can be shared with ChatGPT for post-run analysis.

See the workflow diagrams in [diagrams.md](./diagrams.md#long-run-workflow-test)
for the step flow and document-state view.

## Enable It

The test is skipped by default. Run it explicitly:

```powershell
$env:KOGWISTAR_LLM_WIKI_LONGRUN='1'
.\.venv\Scripts\python.exe -m pytest -m "longrun" tests/integration/test_longrun_workflow_ingestion.py -q -p no:cacheprovider
```

Defaults:

- `KOGWISTAR_OLLAMA_MODEL=gemma4:e2b`
- `KOGWISTAR_OLLAMA_BASE_URL=http://localhost:11434`
- `KOGWISTAR_LONGRUN_DOC_COUNT=20`
- `KOGWISTAR_LONGRUN_MAX_REPEATED_SYSTEMIC_ERRORS=3`
- `KOGWISTAR_LONGRUN_MAX_POST_DOC_MAINTENANCE_STEPS=100`

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
- `graph_export.json`
- `promotion_evidence_pack` records for promoted documents
- `projection_summary.json`
- `maintenance_summary.json`
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
