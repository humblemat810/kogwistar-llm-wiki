# Iterative Maintenance Graph Construction Checklist

## Summary

Add a `kogwistar-llm-wiki` operation mode where ingest can seed a document and
let durable maintenance jobs build, correct, link, and retract graph structure
over time through typed patches.

This is an app-level orchestration feature. It should reuse `kogwistar`
runtime/job primitives and `kg-doc-parser` source-map/parser capabilities.

## Goals

- [ ] Add visible operation modes: `parse_first`, `maintenance_first`, `hybrid`.
- [ ] Keep source maps authoritative.
- [ ] Seed documents before graph-building maintenance.
- [ ] Propose graph changes as typed patches.
- [ ] Validate patches before applying them.
- [ ] Apply patches transactionally when supported.
- [ ] Preserve retractions and supersessions as audit records.
- [ ] Support conversation-scoped maintenance lanes.

## Non-Goals

- [ ] Do not move maintenance policy into `kogwistar`.
- [ ] Do not move parser internals into `kogwistar-llm-wiki`.
- [ ] Do not allow LLM output to directly mutate the graph.
- [ ] Do not require a full initial parse in `maintenance_first` mode.

## Slice 1: Operation Mode Surface

**Goal:** make the ingest behavior explicit.

- [ ] Add `operation_mode` or `ingest_mode` to ingest request models.
- [ ] Support `parse_first`, `maintenance_first`, and `hybrid`.
- [ ] Store selected mode in source document metadata.
- [ ] Include selected mode in ingest result/reporting.
- [ ] Add CLI and VS Code launch selection only after model/API tests pass.
- [ ] Add tests proving existing parse-first behavior remains unchanged.

## Slice 2: Source Map Seed

**Goal:** make minimal ingest useful without a full parse.

- [ ] Ensure source registration can create a source map without writing a full
  parse tree.
- [ ] Write a minimal document node with source identity and source-map pointer.
- [ ] Mark initial document graph status as `seeded`.
- [ ] Persist source-map digest for idempotency.
- [ ] Add tests for maintenance-first source seed creation.

## Slice 3: Maintenance Patch Models

**Goal:** create the typed contract between proposal, validation, and apply.

- [ ] Add `MaintenancePatch`.
- [ ] Add `MaintenancePatchOperation`.
- [ ] Add operation kinds:
  `ADD_NODE`, `UPDATE_NODE`, `REMOVE_NODE`, `ADD_EDGE`, `REMOVE_EDGE`,
  `REPLACE_EDGE`, `ADD_PARSE_CHILD`, `RETRACT_PARSE_CHILD`, `ADD_CROSSLINK`,
  `REMOVE_CROSSLINK`, `MARK_AMBIGUOUS`, `REQUEST_REVIEW`.
- [ ] Add stable `patch_id` and `operation_id`.
- [ ] Add evidence fields:
  `source_document_id`, `source_span_ids`, `maintenance_run_id`, `confidence`.
- [ ] Add patch status:
  `proposed`, `validated`, `partially_accepted`, `applied`, `rejected`,
  `needs_review`, `retracted`.
- [ ] Keep LLM-facing patch proposal schemas strict and JSON-safe.

## Slice 4: Patch Validation

**Goal:** keep graph writes deterministic and accountable.

- [ ] Validate source grounding for every node/edge operation.
- [ ] Validate namespace scope.
- [ ] Validate operation idempotency.
- [ ] Validate edge endpoint existence or same-patch creation.
- [ ] Validate remove/retract operations target existing active facts.
- [ ] Validate replacement/supersession lineage.
- [ ] Reject operations with missing evidence unless explicitly marked
  `REQUEST_REVIEW`.
- [ ] Add tests for invalid grounding, missing endpoints, duplicate operations,
  and cross-namespace writes.

## Slice 5: Patch Application

**Goal:** apply accepted operations as a coherent graph update.

- [ ] Reuse `kogwistar` transaction/runtime primitives where possible.
- [ ] Apply a patch as one unit when backend transaction mode supports it.
- [ ] Make patch application idempotent for retry.
- [ ] Emit patch-applied artifacts.
- [ ] Emit patch-failed artifacts with validation details.
- [ ] Preserve operation-level status for partial acceptance.
- [ ] Add tests for retrying the same patch without duplicate graph objects.

## Slice 6: Maintenance Job Taxonomy

**Goal:** expand beyond current `distill` and `execution_wisdom` jobs.

- [ ] Add `document_seed_graph`.
- [ ] Add `document_expand_parse_children`.
- [ ] Add `document_correct_parse_children`.
- [ ] Add `document_summarize_units`.
- [ ] Add `document_extract_entities`.
- [ ] Add `document_propose_crosslinks`.
- [ ] Add `document_validate_crosslinks`.
- [ ] Add `document_retract_crosslinks`.
- [ ] Add `document_detect_conflicts`.
- [ ] Add `conversation_promote_to_kg`.
- [ ] Add `graph_patch_review`.
- [ ] Add `graph_patch_apply`.
- [ ] Route job kinds to workflow ids in `maintenance_policy.py`.

## Slice 7: Worker Strategy Layer

**Goal:** avoid turning `MaintenanceWorker` into one large switch.

- [ ] Add a maintenance strategy registry.
- [ ] Register current derived-knowledge and execution-wisdom strategies.
- [ ] Add graph-patch proposal strategy.
- [ ] Add graph-patch apply strategy.
- [ ] Keep `MaintenanceDaemon` unchanged except for configuration metadata.
- [ ] Add tests for strategy selection by maintenance kind.

## Slice 8: Cross-Link Maintenance

**Goal:** make links between documents candidate-first and retractable.

- [ ] Add cross-link candidate patch operation.
- [ ] Validate candidate links against source evidence from both sides.
- [ ] Separate candidate cross-links from accepted cross-links.
- [ ] Add retraction operation for stale or incorrect links.
- [ ] Add confidence/review thresholds.
- [ ] Add tests for add, reject, and retract cross-link flows.

## Slice 9: Conversation-Scoped Maintenance

**Goal:** support temporary thread-level graph construction.

- [ ] Add conversation/thread maintenance scope.
- [ ] Keep conversation-scoped facts out of workspace KG until promoted.
- [ ] Add candidate promotion patches from conversation scope to workspace KG.
- [ ] Preserve provenance back to conversation messages and source spans.
- [ ] Add tests proving conversation scope does not leak into workspace KG
  before promotion.

## Slice 10: Status And Reporting

**Goal:** make eventual construction visible.

- [ ] Track document graph status:
  `seeded`, `expanding`, `needs_review`, `stable`, `stale`, `superseded`.
- [ ] Include graph status in ingest result.
- [ ] Include patch counts in maintenance replies.
- [ ] Add report fields for proposed/applied/rejected/retracted operations.
- [ ] Add cost summaries by maintenance kind and model.
- [ ] Add review query support for patch artifacts.

## Slice 11: Migration

**Goal:** introduce the mode without breaking parse-first workflows.

- [ ] Keep `parse_first` as default.
- [ ] Add `maintenance_first` behind explicit flag/config.
- [ ] Add `hybrid` after source seed and patch application tests are stable.
- [ ] Run the same document through all modes and compare:
  initial latency, accepted patch count, fallback rate, graph quality, and
  total model cost.
- [ ] Promote `hybrid` only if it improves local-model reliability.

## Focused Test Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_llm_wiki_cli.py tests\unit\test_provider_config.py -q -p no:cacheprovider
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit tests\integration -q -p no:cacheprovider -k "maintenance or ingest"
```

```powershell
.\.venv\Scripts\python.exe -m pytest kogwistar\tests\runtime -q -p no:cacheprovider
```

## Implementation Notes

- Reuse `kogwistar` for jobs, runtime, event history, budget, recovery, and
  transaction semantics.
- Keep patch proposal policy in `kogwistar-llm-wiki`.
- Keep parser-local source maps and pointer repair in `kg-doc-parser`.
- Make every LLM-proposed operation pass deterministic validation before graph
  mutation.
- Treat retraction as a normal maintenance outcome, not an exceptional cleanup.

