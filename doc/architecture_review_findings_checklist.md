# Architecture Review Findings Traceability Checklist

## Overview

This checklist operationalizes the deep architecture review in
[current_branch_deep_architecture_review.md](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md).
It is the execution tracker for follow-up work on the current branch.

This document is intentionally stricter than a normal backlog:

- every implementation item must map to one or more review findings
- every implementation item must name the expected file touch points up front
- every finding must name verification anchors before it is considered done
- every finding must include a similar-class search before it can be closed

Grouping:

- `Immediate corrections`: high severity and clear semantic drift
- `Near-term hardening`: medium severity correctness and observability gaps
- `Cleanup and alignment`: low/medium documentation, naming, and consistency gaps

Unless explicitly marked otherwise, findings `F1` through `F20` are in scope.

## How To Use This Checklist

1. Start from the matching finding section in this document.
2. Read the linked finding in the architecture review first.
3. Before editing code, expand or confirm the `Primary implementation targets`.
4. Complete the `Similar-class search` and record any sibling issues under
   `Discovered during implementation`.
5. Only then implement the fix and update the matching checkbox items.
6. Run or update the listed regression tests.
7. Mark the finding done only when the `Done means` bar is fully met.

Item format:

- `[ ] Describe the change. Maps: F#. Files: path1, path2.`

Example:

- `[ ] Add honest runtime dependency declaration for imported sibling packages. Maps: F4. Files: pyproject.toml, scripts/bootstrap-dev.sh, README.md.`

## Cross-Finding Search Rule

No finding may be marked done until the implementer has searched for sibling
occurrences of the same problem class.

Rules:

- if the new occurrence is the same root cause, add it under the current finding
- if it is adjacent but distinct, add it to `Shared Search Backlog` or open a
  new finding item in the relevant section
- every finding must carry:
  - at least one `search completed` checkbox
  - at least one `verification completed` checkbox

Required search themes for this branch:

- duplicate-idempotency gaps
- broad fallback exception handling
- workspace/namespace isolation leaks
- stale docs that still imply broad agent/capability registry semantics
- bootstrap/install/import contract drift

## Finding Checklist

### Immediate Corrections

#### F1. Sync ingest is not idempotent for promoted knowledge

- Severity: High
- Source review reference:
  [F1 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F2`, `F6`, `F15`
- Problem summary:
  repeat ingest, retry, or crash-before-ack can create duplicate promoted
  entities and duplicate projection jobs instead of converging.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/ingest_pipeline.py`
  - `src/kogwistar_llm_wiki/policies.py`
  - `src/kogwistar_llm_wiki/projection_worker.py`
  - `tests/unit/test_projection_consistency.py`
  - `tests/unit/test_knowledge_derivation.py`
- Checklist items:
  - [x] Define a stable identity strategy for promoted knowledge writes. Maps: `F1`. Files: `src/kogwistar_llm_wiki/ingest_pipeline.py`, `src/kogwistar_llm_wiki/policies.py`.
  - [x] Decide whether candidate-link and promotion-candidate artifacts also need stable ids or explicit version semantics. Maps: `F1`, `F15`. Files: `src/kogwistar_llm_wiki/ingest_pipeline.py`, `src/kogwistar_llm_wiki/policies.py`.
  - [x] Add or confirm a durable promotion completion marker keyed by source identity and policy version. Maps: `F1`. Files: `src/kogwistar_llm_wiki/ingest_pipeline.py`.
  - [x] Ensure duplicate ingest does not enqueue duplicate projection jobs for the same promoted result. Maps: `F1`, `F6`. Files: `src/kogwistar_llm_wiki/ingest_pipeline.py`, `src/kogwistar_llm_wiki/projection_worker.py`.
  - [x] Add regression coverage for duplicate sync ingest convergence. Maps: `F1`. Files: `tests/unit/test_projection_consistency.py`, `tests/unit/test_knowledge_derivation.py`.
  - [x] Search completed for other duplicate-write paths driven by at-least-once redelivery. Maps: `F1`. Files: review-only search across `src/kogwistar_llm_wiki/`.
  - [x] Verification completed. Maps: `F1`. Files: `tests/unit/test_projection_consistency.py`, `tests/unit/test_knowledge_derivation.py`.
- Similar-class search:
  - Search for other artifact writes that depend on generated ids instead of stable ids.
  - Search patterns: `"_artifact_node("`, `"node_id=None"`, `"stable_id("`, `"promoted_knowledge"`, `"projection job"`.
- Discovered during implementation:
  - [x] Sync ingest now converges by stable IDs for source document, maintenance request, candidate link, promotion candidate, promoted knowledge, maintenance lane request, maintenance job, and projection job. Candidate-link and promotion-candidate artifacts intentionally remain canonical per `workspace_id + source_document_id` rather than versioned in this slice.
  - [x] Duplicate sync ingest also repairs the crash window where the maintenance lane request exists but the durable maintenance job was not enqueued yet; the repaired job reuses the existing lane request message id.
- Regression tests to add/update:
  - `tests/unit/test_projection_consistency.py`
  - `tests/unit/test_knowledge_derivation.py`
- Done means:
  duplicate sync ingest yields one authoritative promoted entity outcome, one
  projection job outcome, and documented semantics for any intentionally
  versioned artifacts.

#### F2. Maintenance lane replies are not deduped across crash/retry

- Severity: High
- Source review reference:
  [F2 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F1`, `F6`, `F7`
- Problem summary:
  a crash after reply send but before job completion can redeliver the same work
  and emit duplicate visible maintenance replies.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/worker.py`
  - `kogwistar/kogwistar/messaging/service.py`
  - `tests/unit/test_lane_message_contract_integration.py`
  - `tests/unit/test_worker_runtime_orchestration.py`
- Checklist items:
  - [x] Add an app-level reply dedupe lookup by `reply_to_message_id + msg_type + correlation_id`. Maps: `F2`. Files: `src/kogwistar_llm_wiki/worker.py`.
  - [x] Evaluate whether core should expose optional lane-message idempotency-key support rather than repeated app-local lookup logic. Maps: `F2`. Files: `kogwistar/kogwistar/messaging/service.py`, `src/kogwistar_llm_wiki/worker.py`.
  - [x] Ensure duplicate successful and failed replies converge after lease redelivery. Maps: `F2`, `F7`. Files: `src/kogwistar_llm_wiki/worker.py`.
  - [x] Add regression coverage for crash-after-reply-before-ack. Maps: `F2`. Files: `tests/unit/test_lane_message_contract_integration.py`, `tests/unit/test_worker_runtime_orchestration.py`.
  - [x] Search completed for other user-visible lane-message writes lacking idempotent convergence. Maps: `F2`. Files: review-only search across `src/kogwistar_llm_wiki/`, `kogwistar/kogwistar/runtime/`.
  - [x] Verification completed. Maps: `F2`. Files: `tests/unit/test_lane_message_contract_integration.py`, `tests/unit/test_worker_runtime_orchestration.py`.
- Similar-class search:
  - Search for every `send_lane_message` call site that can run inside retries, leases, or daemon restarts.
  - Search patterns: `"send_lane_message("`, `"correlation_id="`, `"reply_to_message_id"`, `"mark_done("`, `"lease"`.
- Discovered during implementation:
  - [x] Core `send_lane_message(...)` already supports idempotency-key lookup; llm-wiki now adds an app-level pre-send lookup for visible maintenance replies by both stable idempotency key and `reply_to_message_id + msg_type + correlation_id`.
  - [x] Search found user-visible llm-wiki lane writes only in ingest maintenance requests and maintenance worker replies; runtime `StepContext.send_lane_message(...)` remains a generic workflow helper rather than a concrete retrying app write in this slice.
- Regression tests to add/update:
  - `tests/unit/test_lane_message_contract_integration.py`
  - `tests/unit/test_worker_runtime_orchestration.py`
- Done means:
  repeated execution of the same maintenance completion path does not create
  duplicate foreground replies, and the remaining behavior is explicitly
  documented as at-least-once with convergent output.

#### F3. Recovery run-history inspection is not workspace/namespace isolated

- Severity: High
- Source review reference:
  [F3 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F8`, `F9`, `F10`, `F11`
- Problem summary:
  recovery reports are meant to be workspace-aware, but run-history inspection
  currently ignores scope inputs.
- Primary implementation targets:
  - `kogwistar/kogwistar/engine_core/recovery.py`
  - `kogwistar/kogwistar/server/run_registry.py`
  - `kogwistar/tests/core/test_recovery_subsystem.py`
- Checklist items:
  - [x] Filter recovery run-history rows by workspace and namespace, or add the needed metadata at run creation. Maps: `F3`. Files: `kogwistar/kogwistar/engine_core/recovery.py`, `kogwistar/kogwistar/server/run_registry.py`.
  - [x] Confirm the filtering key source is semantically stable across conversation runs, workflow runs, and server runs. Maps: `F3`. Files: `kogwistar/kogwistar/engine_core/recovery.py`, `kogwistar/kogwistar/server/run_registry.py`.
  - [x] Add multi-workspace regression coverage for recovery inspect and recover-startup reports. Maps: `F3`, `F8`, `F9`. Files: `kogwistar/tests/core/test_recovery_subsystem.py`.
  - [x] Search completed for other recovery inspection surfaces that drop or ignore workspace/namespace inputs. Maps: `F3`, `F10`, `F11`. Files: review-only search across `kogwistar/kogwistar/engine_core/`.
  - [x] Verification completed. Maps: `F3`. Files: `kogwistar/tests/core/test_recovery_subsystem.py`.
- Similar-class search:
  - Search for unused or deleted `workspace_id` and `namespace` parameters in recovery and reporting code.
  - Search patterns: `"del namespace"`, `"del workspace_id"`, `"workspace_id=None"`, `"namespace=None"`, `"list_server_runs"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - `kogwistar/tests/core/test_recovery_subsystem.py`
- Done means:
  recovery reports show only in-scope run history, and cross-workspace leakage
  is covered by regression tests.

#### F4. Package metadata and bootstrap do not match actual runtime imports

- Severity: High
- Source review reference:
  [F4 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F13`, `F14`
- Problem summary:
  install metadata, bootstrap assumptions, and import-time dependencies are not
  aligned, so a clean environment can install successfully and still fail at
  runtime.
  - Primary implementation targets:
    - `pyproject.toml`
    - `scripts/bootstrap-dev.sh`
  - `src/kogwistar_llm_wiki/__init__.py`
  - `src/kogwistar_llm_wiki/ingest_pipeline.py`
  - `src/kogwistar_llm_wiki/projection.py`
  - `src/kogwistar_llm_wiki/models.py`
  - `doc/dev_setup_guide.md`
  - `doc/cli_reference.md`
  - Checklist items:
    - [x] Make runtime dependency declarations honest for imported sibling packages, or make those imports truly lazy/optional. Maps: `F4`. Files: `pyproject.toml`, `src/kogwistar_llm_wiki/ingest_pipeline.py`, `src/kogwistar_llm_wiki/projection.py`, `src/kogwistar_llm_wiki/models.py`.
    - [x] Align top-level Python version metadata with the strictest direct runtime dependency, or lower the dependency requirements upstream if appropriate. Maps: `F4`. Files: `pyproject.toml`.
    - [x] Remove misleading comments that describe direct runtime imports as non-import dependencies. Maps: `F4`. Files: `pyproject.toml`.
    - [x] Fix bootstrap so dependency installation and editable sibling setup match the declared packaging contract. Maps: `F4`. Files: `scripts/bootstrap-dev.sh`, `doc/dev_setup_guide.md`.
    - [x] Add clean import and CLI smoke expectations to docs or tests. Maps: `F4`, `F13`, `F14`. Files: `doc/dev_setup_guide.md`, `doc/cli_reference.md`, `tests/unit/test_llm_wiki_cli.py`, `tests/test_models.py`.
    - [x] Search completed for other import-time sibling dependencies, version drift, and empty dependency declarations that are not actually optional. Maps: `F4`. Files: review-only search across root metadata and `src/`.
    - [x] Verification completed. Maps: `F4`. Files: targeted smoke script or future import-smoke test module.
- Similar-class search:
  - Search for other import-time sibling dependencies that are described as optional or non-runtime.
  - Search patterns: `"from kg_doc_parser"`, `"from kogwistar_obsidian_sink"`, `"dependencies = []"`, `"not a Python import dep"`, `"--no-deps"`.
  - Discovered during implementation:
    - [x] None yet.
- Regression tests to add/update:
  - import smoke coverage for `import kogwistar_llm_wiki`
  - CLI smoke coverage for `llm-wiki --help`
- Done means:
  a clean install path, bootstrap path, and runtime import path tell the same
  story, and the documented Python version contract matches reality.

#### F5. Core has two adjacent service concepts that need explicit separation

- Severity: High
- Source review reference:
  [F5 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F8`, `F12`, `F18`
- Problem summary:
  service-health visibility and workflow-backed service supervision are both in
  core, but their boundaries are not yet clear enough to prevent semantic drift.
  - Primary implementation targets:
    - `kogwistar/kogwistar/server/service_daemon.py`
    - `kogwistar/kogwistar/engine_core/service_health.py`
    - `kogwistar/tests/server/test_service_daemon_model.py`
    - `kogwistar/tests/core/test_service_health_registry.py`
    - `kogwistar/docs/service_daemon_model.md`
    - `kogwistar/docs/recovery_repair_utilities.md`
  - Checklist items:
    - [x] Clarify naming or type aliases so workflow service supervision and service-health visibility cannot be confused. Maps: `F5`. Files: `kogwistar/kogwistar/server/service_daemon.py`, `kogwistar/kogwistar/engine_core/service_health.py`.
    - [x] Add docs that explicitly separate orchestration semantics from health-visibility semantics. Maps: `F5`, `F12`. Files: `kogwistar/docs/service_daemon_model.md`, `kogwistar/docs/recovery_repair_utilities.md`.
    - [x] Add non-interference tests proving recovery reads service health but does not tick, start, or restart supervised services. Maps: `F5`, `F8`. Files: `kogwistar/tests/server/test_service_daemon_model.py`, `kogwistar/tests/core/test_service_health_registry.py`.
    - [x] Search completed for other service-facing APIs or docs that blur health visibility with scheduling or supervision. Maps: `F5`, `F12`. Files: review-only search across `kogwistar/kogwistar/server/`, `kogwistar/docs/`, `doc/`.
    - [x] Verification completed. Maps: `F5`. Files: `kogwistar/tests/server/test_service_daemon_model.py`, `kogwistar/tests/core/test_service_health_registry.py`.
  - Similar-class search:
    - Search for places where `service`, `registry`, `daemon`, `health`, and `supervisor` are used interchangeably.
    - Search patterns: `"ServiceDefinition"`, `"ServiceSupervisor"`, `"ServiceHealthRegistry"`, `"service_registry"`, `"service_health"`.
  - Discovered during implementation:
    - [x] None yet.
- Regression tests to add/update:
  - `kogwistar/tests/server/test_service_daemon_model.py`
  - `kogwistar/tests/core/test_service_health_registry.py`
- Done means:
  service orchestration and service-health visibility are clearly separated in
  naming, docs, and tests, with no accidental cross-mutation.

### Near-Term Hardening

#### F6. Projection manifest is updated before successful materialization

- Severity: Medium/High
- Source review reference:
  [F6 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F1`, `F2`, `F15`
- Problem summary:
  projection manifest state currently overclaims readiness and can treat a
  failed materialization as if it were successfully projected.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/projection_worker.py`
  - `src/kogwistar_llm_wiki/projection.py`
  - `tests/unit/test_projection_consistency.py`
- Checklist items:
  - [x] Separate desired, rebuilding, ready, and failed projection state, or otherwise make manifest reads readiness-aware. Maps: `F6`. Files: `src/kogwistar_llm_wiki/projection_worker.py`, `src/kogwistar_llm_wiki/projection.py`.
  - [x] Ensure failed sink runs do not leave snapshot selection in a falsely ready state. Maps: `F6`. Files: `src/kogwistar_llm_wiki/projection_worker.py`, `src/kogwistar_llm_wiki/projection.py`.
  - [x] Add failure-path regression tests for manifest/vault divergence. Maps: `F6`. Files: `tests/unit/test_projection_consistency.py`.
  - [x] Search completed for other app projections or manifests that mark readiness before durable materialization. Maps: `F6`. Files: review-only search across `src/kogwistar_llm_wiki/`.
  - [x] Verification completed. Maps: `F6`. Files: `tests/unit/test_projection_consistency.py`.
- Similar-class search:
  - Search for writes that update selection/manifest state before a sink or output operation actually completes.
  - Search patterns: `"status=\"rebuilding\""`, `"projected_ids"`, `"materialized_state"`, `"manifest"`, `"sink"`.
- Discovered during implementation:
  - [x] `ProjectionWorker` already wrote readiness-aware manifest state; this slice finalized the reader contract, daemon reporting, and compatibility handling for legacy `projected_ids` rows.
- Regression tests to add/update:
  - `tests/unit/test_projection_consistency.py`
- Done means:
  manifest state clearly distinguishes intent from successful materialization,
  and failed projections no longer look ready.

#### F7. Workflow design lookup errors can leave jobs in DOING without retry/fail accounting

- Severity: Medium/High
- Source review reference:
  [F7 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F2`, `F9`
- Problem summary:
  narrow fallback behavior is correct, but unexpected lookup failures still skip
  immediate queue accounting and leave claimed rows waiting for lease expiry.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/worker.py`
  - `tests/unit/test_worker_runtime_orchestration.py`
  - `tests/unit/test_daemon_interrupt_recovery.py`
- Checklist items:
  - [x] Add a job-level error accounting path that records retry or fail state for unexpected workflow lookup errors without restoring the old broad fallback. Maps: `F7`. Files: `src/kogwistar_llm_wiki/worker.py`.
  - [x] Preserve the narrow recoverable-Chroma rematerialization path while re-raising real corruption or ACL errors. Maps: `F7`, `F9`. Files: `src/kogwistar_llm_wiki/worker.py`.
  - [x] Add regression tests for unrelated lookup failures, queue accounting, and daemon failure visibility. Maps: `F7`. Files: `tests/unit/test_worker_runtime_orchestration.py`, `tests/unit/test_daemon_interrupt_recovery.py`.
  - [x] Search completed for other claimed-job paths that can raise before retry/fail accounting happens. Maps: `F7`. Files: review-only search across queue-processing code.
  - [x] Verification completed. Maps: `F7`. Files: `tests/unit/test_worker_runtime_orchestration.py`, `tests/unit/test_daemon_interrupt_recovery.py`.
- Similar-class search:
  - Search for claim-then-raise paths in workers and daemons.
  - Search patterns: `"claim_"`, `"retry_or_fail("`, `"mark_done("`, `"except Exception"`, `"raise"`.
- Discovered during implementation:
  - Reviewed `src/kogwistar_llm_wiki/projection_worker.py`; its failure path already records retry/fail accounting, so no additional runtime change was needed for this slice.
- Regression tests to add/update:
  - `tests/unit/test_worker_runtime_orchestration.py`
  - `tests/unit/test_daemon_interrupt_recovery.py`
- Done means:
  unexpected lookup failures are visible, narrowly classified, and queue rows get
  durable retry/fail accounting instead of relying only on lease expiry.

#### F8. Service-health projection repair is not represented in recovery actions/counts

- Severity: Medium
- Source review reference:
  [F8 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F3`, `F5`, `F18`
- Problem summary:
  recovery is allowed to repair service-health latest rows, but the resulting
  report does not tell operators that this happened.
- Primary implementation targets:
  - `kogwistar/kogwistar/engine_core/recovery.py`
  - `kogwistar/kogwistar/engine_core/service_health.py`
  - `kogwistar/tests/core/test_recovery_subsystem.py`
- Checklist items:
  - [x] Surface service-health repair actions and counts in `RecoveryReport`. Maps: `F8`. Files: `kogwistar/kogwistar/engine_core/recovery.py`.
  - [x] Reuse the existing repair result shape or add a generic per-surface repair action model. Maps: `F8`. Files: `kogwistar/kogwistar/engine_core/recovery.py`, `kogwistar/kogwistar/engine_core/service_health.py`.
  - [x] Add regression tests for repaired service-health projection rows appearing in startup recovery output. Maps: `F8`. Files: `kogwistar/tests/core/test_recovery_subsystem.py`.
  - [x] Search completed for other mutating recovery operations that are not reported back to operators. Maps: `F8`. Files: review-only search across `kogwistar/kogwistar/engine_core/recovery.py`.
  - [x] Verification completed. Maps: `F8`. Files: `kogwistar/tests/core/test_recovery_subsystem.py`.
- Similar-class search:
  - Search for side-effecting recovery calls whose results are dropped or not counted.
  - Search patterns: `"repair_"`, `"recover_startup"`, `"actions="`, `"repaired_count"`, `"scanned_count"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - `kogwistar/tests/core/test_recovery_subsystem.py`
- Done means:
  startup recovery reports all bounded repairs it performs, including
  service-health projection repair.

#### F9. Checkpoint inspection has a broad namespace fallback that hides real storage errors

- Severity: Medium
- Source review reference:
  [F9 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F3`, `F7`
- Problem summary:
  checkpoint inspection currently catches too broadly, which can hide real
  backend failures and unintentionally read outside the intended namespace.
- Primary implementation targets:
  - `kogwistar/kogwistar/engine_core/recovery.py`
  - `kogwistar/tests/core/test_recovery_subsystem.py`
  - `kogwistar/tests/runtime/test_checkpoint_resume_contract.py`
- Checklist items:
  - [x] Replace the broad checkpoint fallback with a typed or predicate-based recoverable-path check. Maps: `F9`. Files: `kogwistar/kogwistar/engine_core/recovery.py`.
  - [x] Add a recovery finding or explicit error surface for unrelated checkpoint inspection failures. Maps: `F9`. Files: `kogwistar/kogwistar/engine_core/recovery.py`.
  - [x] Add regression coverage for known recoverable lookup failures versus unrelated exceptions. Maps: `F9`, `F7`. Files: `kogwistar/tests/core/test_recovery_subsystem.py`, `kogwistar/tests/runtime/test_checkpoint_resume_contract.py`.
  - [x] Search completed for other broad core fallbacks that silently convert real backend failures into empty data. Maps: `F9`. Files: review-only search across `kogwistar/kogwistar/engine_core/`.
  - [x] Verification completed. Maps: `F9`. Files: `kogwistar/tests/core/test_recovery_subsystem.py`, `kogwistar/tests/runtime/test_checkpoint_resume_contract.py`.
- Similar-class search:
  - Search for `except Exception` in read-path fallback logic throughout recovery and inspection code.
  - Search patterns: `"except Exception"`, `"return []"`, `"fallback"`, `"checkpoint"`, `"read.get_nodes"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - `kogwistar/tests/core/test_recovery_subsystem.py`
  - `kogwistar/tests/runtime/test_checkpoint_resume_contract.py`
- Done means:
  checkpoint inspection falls back only on known recoverable conditions, and
  real backend errors stay visible to operators.

#### F10. Wisdom source query depends only on namespace, not workspace metadata

- Severity: Medium
- Source review reference:
  [F10 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F3`, `F11`
- Problem summary:
  wisdom derivation mostly relies on namespace scoping today, but it lacks cheap
  defense-in-depth workspace filtering in the policy query itself.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/policies.py`
  - `tests/unit/test_llm_wiki_policies.py`
  - `tests/unit/test_knowledge_derivation.py`
- Checklist items:
  - [x] Add or confirm stable workspace metadata on workflow step execution artifacts used by wisdom derivation. Maps: `F10`. Files: `kogwistar/kogwistar/runtime/runtime.py`, `src/kogwistar_llm_wiki/policies.py`, and upstream write sites found during search.
  - [x] Update wisdom source query policy to include workspace filtering, or document and test an equivalent isolation guarantee. Maps: `F10`. Files: `src/kogwistar_llm_wiki/policies.py`.
  - [x] Add regression tests for multi-workspace wisdom isolation. Maps: `F10`. Files: `tests/unit/test_llm_wiki_policies.py`, `tests/unit/test_knowledge_derivation.py`.
  - [x] Search completed for other app policies that drop workspace identity from queries while relying only on namespace. Maps: `F10`, `F11`. Files: review-only search across `src/kogwistar_llm_wiki/policies.py`, `src/kogwistar_llm_wiki/`.
  - [x] Verification completed. Maps: `F10`. Files: `tests/unit/test_llm_wiki_policies.py`, `tests/unit/test_knowledge_derivation.py`.
- Similar-class search:
  - Search for policies and queries that `del workspace_id` or omit workspace metadata despite multi-workspace semantics.
  - Search patterns: `"del workspace_id"`, `"workspace_id"`, `"source_query("`, `"where={"`, `"workflow_step_exec"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - `tests/unit/test_llm_wiki_policies.py`
  - `tests/unit/test_knowledge_derivation.py`
- Done means:
  wisdom derivation is defensively workspace-isolated and the intended
  scoping semantics are covered by tests.

#### F11. Projection snapshot reads all KG edges

- Severity: Medium
- Source review reference:
  [F11 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F3`, `F10`
- Problem summary:
  endpoint filtering protects correctness today, but the current edge scan is
  broader than needed and should either be narrowed or explicitly justified.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/projection.py`
  - `tests/unit/test_projection_consistency.py`
- Checklist items:
  - [x] Filter projection edges by workspace when supported, or document why endpoint filtering is the intended isolation boundary. Maps: `F11`. Files: `src/kogwistar_llm_wiki/projection.py`.
  - [x] Add explicit regression coverage for cross-workspace edge isolation. Maps: `F11`. Files: `tests/unit/test_projection_consistency.py`.
  - [x] Search completed for other projection or snapshot reads that scan globally and rely only on later filtering. Maps: `F11`. Files: review-only search across projection-building code.
  - [x] Verification completed. Maps: `F11`. Files: `tests/unit/test_projection_consistency.py`.
- Similar-class search:
  - Search for `where={}` or equivalent broad reads in projection and export surfaces.
  - Search patterns: `"where={}"`, `"get_edges"`, `"projection snapshot"`, `"visible_ids"`.
- Discovered during implementation:
  - [x] Edge rows do not currently carry a reliable workspace filter, so projection keeps the broad scan and enforces isolation by endpoint scope. Files: `src/kogwistar_llm_wiki/projection.py`.
- Regression tests to add/update:
  - `tests/unit/test_projection_consistency.py`
- Done means:
  projection snapshot isolation is either narrowed in code or made explicit and
  defended with tests.

#### F12. AI OS docs still recommend broad agent/capability registries

- Severity: Medium
- Source review reference:
  [F12 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F5`, `F20`
- Problem summary:
  some planning docs still point contributors toward the broader actor/agent
  registry direction that this branch intentionally narrowed away from.
- Primary implementation targets:
  - `doc/ai_os_gap_analysis.md`
  - `doc/ai_os_roadmap.md`
  - `doc/architecture.md`
  - `doc/diagrams.md`
- Checklist items:
  - [ ] Rewrite roadmap and gap-analysis sections so they start from existing identities before proposing new ontologies. Maps: `F12`. Files: `doc/ai_os_gap_analysis.md`, `doc/ai_os_roadmap.md`.
  - [ ] Reframe architecture doc wording that still says "agent" when the semantics are maintenance daemons, workers, runtimes, or service health. Maps: `F12`, `F20`. Files: `doc/architecture.md`, `doc/diagrams.md`.
  - [ ] Add the branch-approved wording distinguishing workflow, runtime, and service health. Maps: `F12`. Files: `doc/ai_os_gap_analysis.md`, `doc/ai_os_roadmap.md`, `doc/architecture.md`.
  - [ ] Search completed for stale agent/capability/participant registry language across docs and tutorials. Maps: `F12`, `F20`. Files: review-only search across `doc/`, `kogwistar/docs/`.
  - [ ] Verification completed. Maps: `F12`. Files: docs-only review and link integrity where applicable.
- Similar-class search:
  - Search for docs that recommend capability discovery, agent registries, participant ontologies, or service semantics broader than the implemented code.
  - Search patterns: `"agent registry"`, `"capability registry"`, `"participant"`, `"actor registry"`, `"capabilities"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - docs-only; no code-path regression required unless terminology affects rendered tutorial integrity tests
- Done means:
  planning docs align with the narrower service-health semantics and no longer
  pull the design back toward frameworkification.

#### F13. CLI docs and implementation disagree on `KOGWISTAR_DATA_DIR`

- Severity: Medium
- Source review reference:
  [F13 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F4`, `F14`
- Problem summary:
  CLI docs claim an env-var fallback that the current implementation does not
  appear to use.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/__main__.py`
  - `doc/cli_reference.md`
  - `tests/unit/test_llm_wiki_cli.py`
- Checklist items:
  - [x] Decide whether `KOGWISTAR_DATA_DIR` is a real supported CLI input or a docs mistake. Maps: `F13`. Files: `src/kogwistar_llm_wiki/__main__.py`, `doc/cli_reference.md`.
  - [x] Implement the chosen contract or remove the false claim. Maps: `F13`. Files: `src/kogwistar_llm_wiki/__main__.py`, `doc/cli_reference.md`.
  - [x] Add CLI regression tests for env-var and no-env behavior. Maps: `F13`. Files: `tests/unit/test_llm_wiki_cli.py`.
  - [x] Search completed for other env vars or defaults described in docs but not implemented. Maps: `F13`, `F14`. Files: review-only search across CLI docs and entrypoints.
  - [x] Verification completed. Maps: `F13`. Files: `tests/unit/test_llm_wiki_cli.py`.
- Similar-class search:
  - Search for CLI or docs claims using "expects", "supports", or "defaults to" that are not backed by parser code.
  - Search patterns: `"KOGWISTAR_DATA_DIR"`, `"os.environ"`, `"ArgumentParser"`, `"default=None"`, `"--data-dir"`.
- Discovered during implementation:
  - `src/kogwistar_llm_wiki/__main__.py` now treats `--data-dir` as the explicit override and `KOGWISTAR_DATA_DIR` as the persistent fallback.
- Regression tests to add/update:
  - `tests/unit/test_llm_wiki_cli.py`
- Done means:
  CLI docs and implementation agree on the data-dir contract, and the behavior
  is covered by tests.

#### F14. `doc/cli_reference.md` programmatic API snippet is stale

- Severity: Medium
- Source review reference:
  [F14 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F4`, `F13`
- Problem summary:
  the programmatic API example no longer matches the actual constructor contract.
- Primary implementation targets:
  - `doc/cli_reference.md`
  - `doc/dev_setup_guide.md`
- Checklist items:
  - [x] Replace stale API snippets with a working `NamespaceEngines`-based example. Maps: `F14`. Files: `doc/cli_reference.md`.
  - [x] Check adjacent setup docs for other outdated programmatic examples introduced before the current constructor shape. Maps: `F14`, `F4`. Files: `doc/dev_setup_guide.md`, `doc/cli_reference.md`.
  - [x] Search completed for stale code samples that still assume direct constructor shortcuts no longer supported. Maps: `F14`. Files: review-only search across `doc/` and `kogwistar/docs/`.
  - [x] Verification completed. Maps: `F14`. Files: docs review or future snippet smoke checks.
- Similar-class search:
  - Search for code snippets that instantiate runtime or app classes with old signatures.
  - Search patterns: `"IngestPipeline("`, `"workspace_id="`, `"build_persistent_namespace_engines"`, `"NamespaceEngines"`.
- Discovered during implementation:
  - [x] The programmatic API example now shows the real `NamespaceEngines` construction path instead of the stale shortcut form.
- Regression tests to add/update:
  - docs-only unless snippet-smoke automation is later added
- Done means:
  the public programmatic API example is copy-pasteable against the current code.

#### F15. Regular ingest and demo ingest have different KG shape

- Severity: Medium
- Source review reference:
  [F15 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F1`, `F6`
- Problem summary:
  demo ingest currently materializes a richer graph than regular sync ingest,
  and that distinction is not yet fully normalized or documented.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/__main__.py`
  - `src/kogwistar_llm_wiki/ingest_pipeline.py`
  - `doc/architecture.md`
  - `doc/happy_paths.md`
  - `tests/unit/test_projection_consistency.py`
- Checklist items:
  - [ ] Decide whether the current ingest-shape difference is intentional product policy or technical drift. Maps: `F15`. Files: `src/kogwistar_llm_wiki/__main__.py`, `src/kogwistar_llm_wiki/ingest_pipeline.py`, `doc/architecture.md`.
  - [ ] If intentional, document the difference clearly in architecture and quickstart-style docs. Maps: `F15`. Files: `doc/architecture.md`, `doc/happy_paths.md`.
  - [ ] If not intentional, plan the normalization path and expected tests before code changes. Maps: `F15`, `F1`. Files: `src/kogwistar_llm_wiki/ingest_pipeline.py`, `tests/unit/test_projection_consistency.py`.
  - [ ] Search completed for other demo-only code paths that create misleading richer semantics than production flows. Maps: `F15`. Files: review-only search across demos and CLI commands.
  - [ ] Verification completed. Maps: `F15`. Files: `tests/unit/test_projection_consistency.py` or docs review depending on chosen resolution.
- Similar-class search:
  - Search for `demo` code paths that write graph state differently than mainline app workflows.
  - Search patterns: `"persist_demo_graph_extraction"`, `"demo"`, `"sync ingest"`, `"promoted_knowledge"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - `tests/unit/test_projection_consistency.py`
- Done means:
  demo-versus-regular ingest semantics are either aligned or explicitly documented
  as a deliberate product distinction.

### Cleanup And Alignment

#### F16. `DefaultPromotionPolicy` still owns a mode string

- Severity: Low/Medium
- Source review reference:
  [F16 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F1`
- Problem summary:
  core policy defaults are now mostly generic, but promotion still keys off an
  app-shaped mode string.
- Primary implementation targets:
  - `kogwistar/kogwistar/policy/__init__.py`
  - `kogwistar/tests/core/test_knowledge_policy_defaults.py`
  - `src/kogwistar_llm_wiki/policies.py`
- Checklist items:
  - [x] Replace the core mode-string check with a more generic explicit positive-signal contract, if that can be done without semantic regression. Maps: `F16`. Files: `kogwistar/kogwistar/policy/__init__.py`, `src/kogwistar_llm_wiki/policies.py`.
  - [x] Update core policy tests and llm-wiki adapters accordingly. Maps: `F16`. Files: `kogwistar/tests/core/test_knowledge_policy_defaults.py`, `src/kogwistar_llm_wiki/policies.py`.
  - [x] Search completed for other app-shaped vocabulary still living in core policy defaults. Maps: `F16`. Files: review-only search across `kogwistar/kogwistar/policy/`.
  - [x] Verification completed. Maps: `F16`. Files: `kogwistar/tests/core/test_knowledge_policy_defaults.py`.
- Similar-class search:
  - Search for string literals in core policy defaults that express app intent instead of generic decision signals.
  - Search patterns: `"sync"`, `"promotion_mode"`, `"artifact_kind"`, `"knowledge_stage"`.
- Discovered during implementation:
  - [x] llm-wiki still intentionally uses `promotion_mode="sync"` at the app layer, but core now receives a generic `promotion_approved` signal instead of owning the product vocabulary.
- Regression tests to add/update:
  - `kogwistar/tests/core/test_knowledge_policy_defaults.py`
- Done means:
  core policy defaults stay generic, and llm-wiki-specific mapping lives in the app.

#### F17. `JobQueueSubsystem.enqueue(...)` silently no-ops when metastore lacks queue support

- Severity: Low/Medium
- Source review reference:
  [F17 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F7`
- Problem summary:
  silent no-op behavior is convenient for optional subsystems but too weak for
  app-critical durable-worker paths.
- Primary implementation targets:
  - `kogwistar/kogwistar/engine_core/jobs.py`
  - `src/kogwistar_llm_wiki/worker.py`
  - `tests/unit/test_worker_runtime_orchestration.py`
- Checklist items:
  - [x] Decide whether core needs a strict mode or typed exception for unavailable durable queue support. Maps: `F17`. Files: `kogwistar/kogwistar/engine_core/jobs.py`.
  - [x] Make llm-wiki startup or worker-critical paths fail fast when durable queue support is required but absent. Maps: `F17`. Files: `src/kogwistar_llm_wiki/worker.py`.
  - [x] Add tests for unavailable queue support in app-critical paths. Maps: `F17`. Files: `tests/unit/test_worker_runtime_orchestration.py`.
  - [x] Search completed for other core subsystem adapters that silently degrade in app-critical flows. Maps: `F17`. Files: review-only search across `kogwistar/kogwistar/engine_core/`.
  - [x] Verification completed. Maps: `F17`. Files: `tests/unit/test_worker_runtime_orchestration.py`.
- Similar-class search:
  - Search for optional subsystem adapters that return empty or falsey values instead of raising in critical paths.
  - Search patterns: `"return \"\""`, `"hasattr("`, `"if not"`, `"Unavailable"`, `"optional subsystem"`.
- Discovered during implementation:
  - [x] Reviewed adjacent optional-return adapters in indexing/meta plumbing; they remain outside this queue-specific slice and can be revisited separately if they become app-critical.
- Regression tests to add/update:
  - `tests/unit/test_worker_runtime_orchestration.py`
- Done means:
  critical durable-worker paths fail loudly when queue support is unavailable.

#### F18. Service-health repair reconstructs coarse state, not latest heartbeat freshness

- Severity: Low/Medium
- Source review reference:
  [F18 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F5`, `F8`
- Problem summary:
  repair from sparse lifecycle facts is semantically correct, but it cannot
  recreate every heartbeat freshness detail once latest-state projection rows are lost.
- Primary implementation targets:
  - `kogwistar/kogwistar/engine_core/service_health.py`
  - `kogwistar/docs/recovery_repair_utilities.md`
  - `kogwistar/docs/tutorials/26_recovery_and_durable_operational_state.md`
- Checklist items:
  - [ ] Document the repaired-service-health freshness limitation clearly in core recovery docs and tutorial material. Maps: `F18`. Files: `kogwistar/docs/recovery_repair_utilities.md`, `kogwistar/docs/tutorials/26_recovery_and_durable_operational_state.md`.
  - [ ] Evaluate whether sparse lifecycle event timestamps should be used as the best available repaired observation time. Maps: `F18`. Files: `kogwistar/kogwistar/engine_core/service_health.py`.
  - [ ] Search completed for other repaired projections whose reconstructed state is intentionally coarser than the original latest-state row. Maps: `F18`, `F8`. Files: review-only search across projection repair code.
  - [ ] Verification completed. Maps: `F18`. Files: docs review and any matching service-health regression updates.
- Similar-class search:
  - Search for repair paths that intentionally rebuild from sparse truth and therefore cannot restore transient latest-state detail.
  - Search patterns: `"repair_projection"`, `"last_seen_ms"`, `"heartbeat"`, `"lifecycle event"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - `kogwistar/tests/core/test_service_health_registry.py` if behavior changes
  - docs updates otherwise
- Done means:
  the coarse-repair semantics are explicit, and any best-available timestamp
  improvement is intentional and tested.

#### F19. Workspace namespace helper still carries legacy projection policy

- Severity: Low
- Source review reference:
  [F19 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F12`
- Problem summary:
  a leftover helper can invite new callers to bypass the newer projection policy path.
- Primary implementation targets:
  - `src/kogwistar_llm_wiki/namespaces.py`
  - `src/kogwistar_llm_wiki/policies.py`
  - `tests/unit/test_llm_wiki_policies.py`
- Checklist items:
  - [x] Remove, deprecate, or clearly fence the legacy helper so projection visibility decisions stay policy-owned. Maps: `F19`. Files: `src/kogwistar_llm_wiki/namespaces.py`, `src/kogwistar_llm_wiki/policies.py`.
  - [x] Add or update tests if helper removal changes public expectations. Maps: `F19`. Files: `tests/unit/test_llm_wiki_policies.py`.
  - [x] Search completed for call sites still using legacy namespace visibility shortcuts instead of policy objects. Maps: `F19`. Files: review-only search across `src/kogwistar_llm_wiki/`.
  - [x] Verification completed. Maps: `F19`. Files: `tests/unit/test_llm_wiki_policies.py`.
- Similar-class search:
  - Search for legacy helper methods that predate the policy-protocol refactor and can bypass policy objects.
  - Search patterns: `"is_kg_visible"`, `"visibility"`, `"projection policy"`.
- Discovered during implementation:
  - [x] `WorkspaceNamespaces` no longer carries policy-shaped visibility helpers; projection eligibility is only owned by `LlmWikiPolicies`.
- Regression tests to add/update:
  - `tests/unit/test_llm_wiki_policies.py`
- Done means:
  projection visibility decisions have one clear policy owner.

#### F20. Lane anchor nodes use `lane_actor` wording

- Severity: Low
- Source review reference:
  [F20 in architecture review](/c:/Users/chanh/Documents/kogwistar-llm-wiki/doc/current_branch_deep_architecture_review.md)
- Related findings: `F12`
- Problem summary:
  the current wording is local and historical, but it can confuse contributors
  now that broader actor/participant registry semantics were intentionally rejected.
- Primary implementation targets:
  - `kogwistar/kogwistar/messaging/service.py`
  - `doc/architecture.md`
  - `doc/diagrams.md`
  - `kogwistar/docs/tutorials/23_lane_messaging_contract.md`
- Checklist items:
  - [ ] Decide whether code renaming is worth the migration cost or whether docs-only clarification is sufficient. Maps: `F20`. Files: `kogwistar/kogwistar/messaging/service.py`, `doc/architecture.md`, `doc/diagrams.md`.
  - [ ] Update docs and diagrams to describe these nodes as lane sender/recipient anchors rather than universal actors. Maps: `F20`, `F12`. Files: `doc/architecture.md`, `doc/diagrams.md`, `kogwistar/docs/tutorials/23_lane_messaging_contract.md`.
  - [ ] Search completed for other doc wording that overgeneralizes lane-local actor terms into broader ontology language. Maps: `F20`. Files: review-only search across docs and lane tutorials.
  - [ ] Verification completed. Maps: `F20`. Files: docs review.
- Similar-class search:
  - Search for `actor` wording in lane docs, diagrams, and service-health discussions.
  - Search patterns: `"lane_actor"`, `"actor"`, `"participant"`, `"sender/recipient"`.
- Discovered during implementation:
  - [ ] None yet.
- Regression tests to add/update:
  - docs-only unless a code rename is chosen
- Done means:
  lane anchor wording no longer suggests a new universal actor model.

#### F21. Core read API conflates probe semantics with full hydrated node semantics

- Severity: Medium
- Source review reference:
  Discovered during `F7` implementation while tracing the workflow-design lookup
  path and the narrow `"Missing Embeddings"` fallback in
  [worker.py](/c:/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/worker.py).
- Related findings: `F7`, `F9`
- Problem summary:
  some callers use `get_nodes(...)` as an existence or control-flow probe, but
  the current read hydration path still expects full node payload shape
  including embeddings. That couples probe semantics to vector-materialization
  state and leaks backend hydration failures into app-level orchestration logic.
- Primary implementation targets:
  - `kogwistar/kogwistar/engine_core/subsystems/read.py`
  - `src/kogwistar_llm_wiki/worker.py`
  - `src/kogwistar_llm_wiki/ingest_pipeline.py`
  - `kogwistar/kogwistar/runtime/design.py`
  - `kogwistar/kogwistar/runtime/runtime.py`
- Checklist items:
  - [x] Decide whether core should expose an explicit probe/metadata-only read path, or whether `get_nodes(...)` should support a lightweight non-hydrating mode. Maps: `F21`. Files: `kogwistar/kogwistar/engine_core/subsystems/read.py`.
  - [x] Update workflow-design existence checks and similar control-flow probes to stop depending on full hydrated node reads when only presence or metadata is needed. Maps: `F21`, `F7`. Files: `src/kogwistar_llm_wiki/worker.py`, `kogwistar/kogwistar/runtime/design.py`.
  - [x] Add regression coverage proving probe-style reads do not require embeddings and do not mutate from partial node views. Maps: `F21`. Files: `kogwistar/tests/core/`, `tests/unit/test_worker_runtime_orchestration.py`.
  - [x] Search completed for other callers using `get_nodes(...)` as a probe rather than a full transferable node read. Maps: `F21`. Files: review-only search across `kogwistar/kogwistar/`, `src/kogwistar_llm_wiki/`.
  - [x] Verification completed. Maps: `F21`. Files: matching core and app regression modules chosen during implementation.
- Similar-class search:
  - Search for read paths that only need existence, ids, or metadata but still hydrate full node payloads.
  - Search patterns: `"get_nodes("`, `"ids=["`, `"limit=1"`, `"workflow_node"`, `"Missing Embeddings"`.
- Discovered during implementation:
  - `kogwistar/kogwistar/runtime/runtime.py` now uses `read.node_exists(...)` / `read.edge_exists(...)` for conversation existence checks instead of hydrating `get_nodes(...)` / `get_edges(...)`.
  - `kogwistar/kogwistar/runtime/replay.py` and `kogwistar/kogwistar/server/chat_service.py` now use metadata-only probe reads where only checkpoint/message metadata was needed.
- Regression tests to add/update:
  - `tests/unit/test_worker_runtime_orchestration.py`
  - one new or existing focused core read-contract module under `kogwistar/tests/core/`
- Done means:
  probe reads and full transferable node reads are semantically separated enough
  that control-flow checks no longer depend on embedding hydration.

## Shared Search Backlog

- [ ] Search for duplicate-idempotency gaps outside ingest and maintenance reply flows. Candidate roots: `src/kogwistar_llm_wiki/ingest_pipeline.py`, `src/kogwistar_llm_wiki/worker.py`, `src/kogwistar_llm_wiki/projection_worker.py`.
- [ ] Search for broad fallback exception handling in core and app recovery/read paths. Candidate roots: `kogwistar/kogwistar/engine_core/recovery.py`, `src/kogwistar_llm_wiki/worker.py`.
- [ ] Search for workspace/namespace isolation leaks across recovery, projection, and policy query code. Candidate roots: `kogwistar/kogwistar/engine_core/recovery.py`, `src/kogwistar_llm_wiki/policies.py`, `src/kogwistar_llm_wiki/projection.py`.
- [ ] Search for stale docs that still imply agent/capability registry semantics. Candidate roots: `doc/ai_os_gap_analysis.md`, `doc/ai_os_roadmap.md`, `doc/architecture.md`, `doc/diagrams.md`.
- [ ] Search for bootstrap/install/import contract drift across app docs, scripts, and metadata. Candidate roots: `pyproject.toml`, `scripts/bootstrap-dev.sh`, `doc/dev_setup_guide.md`, `doc/cli_reference.md`.

## Verification Anchors

High-severity findings should name at least one concrete regression module.
Medium-severity findings should name either a regression module or an explicit
docs-only reason.

Recommended anchor modules for this branch:

- `tests/unit/test_lane_message_contract_integration.py`
- `tests/unit/test_daemon_interrupt_recovery.py`
- `tests/unit/test_projection_consistency.py`
- `tests/unit/test_worker_runtime_orchestration.py`
- `tests/unit/test_llm_wiki_cli.py`
- `tests/unit/test_llm_wiki_policies.py`
- `tests/unit/test_knowledge_derivation.py`
- `kogwistar/tests/core/test_recovery_subsystem.py`
- `kogwistar/tests/core/test_service_health_registry.py`
- `kogwistar/tests/core/test_knowledge_policy_defaults.py`
- `kogwistar/tests/runtime/test_checkpoint_resume_contract.py`
- `kogwistar/tests/server/test_service_daemon_model.py`

## Completion Rule

A finding is complete only when all of the following are true:

- the linked review finding still matches the implemented scope
- implementation items are checked off and expected file touch points were real
- the similar-class search was completed and any new sibling issues were added to
  this checklist or to the shared backlog
- verification was completed and recorded
- docs and diagrams were updated if the finding changes public semantics
- any unresolved residual risk is called out explicitly rather than silently
  treated as fixed
