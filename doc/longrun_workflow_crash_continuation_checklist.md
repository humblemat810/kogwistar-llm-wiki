# Long-Run Workflow Crash-Continuation Checklist

## Goal

Align the long-run workflow test with the existing `kogwistar` crash-continuation semantics instead of inventing a separate harness-level resume system.

The key decision is:

- keep the workflow design as-is
- rely on existing runtime state snapshots and continuation behavior
- make the harness observable and diagnosable across interrupted runs
- avoid adding a new fake resume framework unless the current runtime path truly cannot support the use case

## Guardrail First

- [x] Remove or back out any harness-only resume mechanism that bypasses the existing runtime continuation model.
- [x] Keep the `noop` registration fix if it is required for the current workflow design to validate.
- [x] Confirm no workflow topology changes were made for this effort.
- [x] Confirm no extra workflow steps were added just to support resume.

## Understand Existing Continuation Semantics

- [ ] Identify exactly where `kogwistar` stores step-level runtime state, checkpoints, and workflow progress.
- [ ] Document whether continuation is driven by:
  - workflow run records
  - checkpoints
  - durable queues
  - projected lane rows
  - some combination of the above
- [ ] Verify whether continuation expects:
  - same `run_id`
  - same `conversation_id`
  - same `turn_node_id`
  - same workflow graph
- [ ] Verify whether the runtime can resume a partially completed run automatically, or whether recovery code must be called explicitly.

## Harness Behavior Audit

- [ ] Review the long-run harness to see where it currently assumes a fresh start.
- [ ] Identify which parts are incompatible with crash continuation:
  - fresh `tmp_path`
  - in-memory engines
  - regenerated corpus
  - new `run_id`
  - new conversation state
- [ ] Separate "test isolation convenience" from "actual continuation blocker."
- [ ] Write down the minimum changes needed so the harness can re-enter an interrupted run using existing runtime semantics.

## Stable Run Identity

- [ ] Decide the stable identity boundary for one long-run execution:
  - run directory
  - workspace id
  - workflow id
  - conversation id
  - document ids
- [ ] Ensure those identities are deterministic across reruns of the same interrupted long-run session.
- [ ] Confirm which IDs may change safely and which must stay fixed for continuation to work.

## Observable Progress

- [x] Add explicit progress checkpoints in the harness dump without inventing new workflow semantics.
- [x] Ensure each return to terminal leaves enough evidence to answer:
  - which document was active
  - which step was last reached
  - whether any document progressed since the previous attempt
  - whether maintenance or projection progressed
- [x] Make progress visible in:
  - `manifest.jsonl`
  - `status_transitions.jsonl`
  - `final_report.md` or an interim report
- [x] Add per-document timing so we can tell "slow Ollama parse" from "stuck runtime."

## Crash-Reentry Contract

- [ ] Define what should happen if the process is interrupted during:
  - `claim_document`
  - `parse_document`
  - `persist_document`
  - `enqueue_background_maintenance`
  - `observe_background_maintenance`
  - `verify_document_artifacts`
- [ ] Define whether a doc in `processing/` should be:
  - resumed in place
  - retried from a known safe step
  - quarantined only after recovery logic fails
- [ ] Confirm the harness follows existing project conventions for interrupted in-flight work instead of inventing new folder semantics.

## Recovery Hooking

- [ ] Identify whether the harness should invoke existing recovery APIs on startup for an interrupted run.
- [ ] If yes, use those APIs directly instead of replaying ad hoc logic.
- [ ] If no, document why existing recovery does not apply to this long-run path.
- [x] Verify that recovery results are included in the diagnostic dump.

## Timeout-Probe Strategy

- [ ] Design an intentional "progressive probe" mode for local diagnosis:
  - run for a bounded window
  - return control
  - inspect state
  - rerun against the same interrupted session
- [ ] Ensure this probe mode uses the same runtime/recovery semantics as the full long run.
- [ ] Do not treat probe mode as a separate implementation path.

## Tests To Add

- [x] Add a focused integration test that simulates interruption after at least one step and verifies later continuation progresses further.
- [x] Add a test that proves interrupted docs do not silently reset to fresh `PENDING` when continuation should resume them.
- [x] Add a test that proves repeated probe runs produce cumulative progress evidence.
- [x] Add a test that distinguishes:
  - real continuation
  - accidental fresh start
- [ ] Keep full Ollama long-run coverage opt-in, but add at least one smaller interruption/continuation regression test that can run without a full soak.

## Documentation

- [ ] Update the long-run doc to explain:
  - whether crash continuation is supported
  - what is required for it to work
  - how to tell resumed progress from a fresh run
- [ ] Document the exact operator workflow for interrupted long runs.
- [ ] Add a note describing what the harness should and should not own versus what `kogwistar` runtime/recovery already owns.

## Acceptance Criteria

- [ ] The harness does not pretend to resume by rebuilding state from scratch unless that is explicitly the approved behavior.
- [ ] A repeated run after interruption can show real progress using existing `kogwistar` continuation semantics.
- [ ] The dump makes it obvious whether the run resumed, restarted, or stalled.
- [ ] No workflow design changes are introduced just to support continuation.
- [ ] The only harness changes are observability and correct use of existing recovery/runtime behavior.
