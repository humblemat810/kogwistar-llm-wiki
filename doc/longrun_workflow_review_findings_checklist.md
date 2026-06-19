# Long-Run Workflow Review Findings Checklist

## Summary

This checklist records the current review findings for
`tests/integration/test_longrun_workflow_ingestion.py`.

The harness is useful as a diagnostic shell and now has a controlled runtime
resume probe. The important distinction is:

- manifest-aware rerun works for observing prior document states and restoring
  operator-facing dump history
- checkpoint reruns now preserve prior transition and failure history in the
  dump
- true runtime checkpoint continuation is wired for the deliberate
  `await_resume` suspension point after parsed graph persistence
- promotion evidence-pack provenance is now wired through the long-run harness
  as well as production ingest
- maintenance proof is now keyed to maintenance-specific signals instead of
  generic ingest workflow steps
- the remaining review items are live Ollama probe and soak verification below

## Promotion Provenance

- [x] In `enqueue_background_maintenance`, call
  `create_promotion_evidence_pack(...)` after `graph_extraction` is available
  and before `create_promotion_candidate(...)`.
- [x] Pass `promotion_evidence_pack_id` and
  `promotion_evidence_pack_digest` into `create_promotion_candidate(...)`.
- [x] Pass the same evidence-pack id and digest into `promote_to_knowledge(...)`.
- [x] Store `promotion_evidence_pack_id` on the long-run `DocumentRecord` or
  dump manifest if that makes diagnosis easier.
- [x] Add a long-run harness regression proving promoted nodes created by the
  harness always reference a `promotion_evidence_pack`.
- [x] Add a dump/invariant assertion that promoted nodes without a promotion
  evidence pack are a correctness failure.

## Runtime Continuation

- [x] Decide and document whether the long-run harness supports true runtime
  checkpoint continuation or only idempotent rerun from manifest state.
- [x] Make the long-run harness backend-selectable for any non-volatile backend.
  The harness must not be hard-wired to folder-backed persistent Chroma if
  PostgreSQL or another durable backend is available, because crash
  continuation cannot be validated against a volatile in-memory backend.
- [x] If true continuation is required, use existing `kogwistar` runtime
  checkpoint/replay APIs instead of only loading `dump/manifest.jsonl`.
- [x] Preserve the same `run_id`, `conversation_id`, `turn_node_id`, and
  workflow graph when resuming an interrupted workflow run.
- [x] Rehydrate persisted parsed graph state and call
  `WorkflowRuntime.resume_run(...)` for an interrupted document.
- [x] Add a regression that interrupts after a completed workflow step, reruns,
  and proves the next invocation resumes from runtime checkpoint state instead
  of executing from a fresh initial state.
- [x] If the current behavior remains manifest rerun, rename the docs and report
  fields so they do not imply true checkpoint continuation.

## Maintenance Proof

- [x] Tighten `maintenance_summary()` so `workflow_step_count` only counts
  maintenance workflow step executions, not document-ingestion workflow steps.
- [x] Require at least one durable maintenance-specific signal:
  `derived_knowledge`, completed maintenance job, foreground maintenance reply,
  or maintenance event linked to the source document/workspace.
- [x] Add a regression where ingest workflow steps exist but maintenance has not
  run, and verify the long-run invariant fails.
- [x] Include maintenance job ids and source document ids in the final report so
  useful work can be traced back to documents.

## Dump Continuity

- [x] On checkpoint load, rehydrate `status_transitions.jsonl` into
  `status_transitions`.
- [x] On checkpoint load, rehydrate `failure_records.jsonl` into
  `failure_records`.
- [x] Preserve prior transition/failure history when a continue run writes a new
  dump.
- [x] Add a regression proving a continue run does not overwrite prior history
  with only the current process's in-memory events.

## Fresh Start Semantics

- [x] Reset the run directory before persistent namespace engines are opened in
  fresh mode.
- [x] Avoid deleting an already-open persistent engine directory on Windows.
- [x] Add a regression that stale engine state is absent after a fresh start.
- [x] Keep continue mode strict: it should fail loudly when no compatible
  checkpoint is present.
- [x] Keep auto mode forgiving: it should fall back to fresh when the checkpoint
  document count does not match the requested corpus size.

## Operator Entry Points

- [x] Replace the unreliable VSCode `tasks.json` entry point with VSCode launch
  configurations.
- [x] Provide fresh and continue launch configurations for 20, 3, and 1 document
  corpora.
- [x] Document which launch configurations are diagnostic probes and which one
  satisfies the full 20-document acceptance criterion.

## Verification

- [x] Run the focused non-Ollama harness tests after the fixes.
- [ ] Run the 1-document Ollama launch configuration and inspect the dump for
  real parse, persistence, maintenance, and projection evidence.
- [ ] Run the 3-document Ollama launch configuration after the 1-document probe
  succeeds.
- [ ] Run the 20-document long-run only after the smaller probes show the
  harness is exercising the intended path.
