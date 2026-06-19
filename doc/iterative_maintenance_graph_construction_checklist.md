# Iterative Maintenance Graph Construction Checklist

## Summary

Add a `kogwistar-llm-wiki` operation mode where ingest can seed a document and
let durable maintenance jobs build, correct, link, and retract graph structure
over time through typed patches.

This is an app-level orchestration feature. It should reuse `kogwistar`
runtime/job primitives and `kg-doc-parser` source-map/parser capabilities.

## Goals

- [x] Add visible operation modes: `parse_first`, `maintenance_first`, `hybrid`.
- [x] Keep source maps authoritative.
- [x] Seed documents before graph-building maintenance.
- [x] Propose graph changes as typed patches.
- [x] Separate high-level maintenance intent from low-level graph operations.
- [x] Validate patches before applying them.
- [x] Apply patches transactionally when supported.
- [x] Preserve retractions and supersessions as audit records.
- [x] Support conversation-scoped maintenance lanes.

## Non-Goals

- [x] Do not move maintenance policy into `kogwistar`.
- [x] Do not move parser internals into `kogwistar-llm-wiki`.
- [x] Do not allow LLM output to directly mutate the graph.
- [x] Do not require a full initial parse in `maintenance_first` mode.

## Slice 1: Operation Mode Surface

**Goal:** make the ingest behavior explicit.

- [x] Add `operation_mode` or `ingest_mode` to ingest request models.
- [x] Support `parse_first`, `maintenance_first`, and `hybrid`.
- [x] Store selected mode in source document metadata.
- [x] Include selected mode in ingest result/reporting.
- [x] Add CLI and VS Code launch selection only after model/API tests pass.
- [x] Add tests proving existing parse-first behavior remains unchanged.

## Slice 2: Source Map Seed

**Goal:** make minimal ingest useful without a full parse.

- [x] Ensure source registration can create a source map without writing a full
  parse tree.
- [x] Write a minimal document node with source identity and source-map pointer.
- [x] Mark initial document graph status as `seeded`.
- [x] Persist source-map digest for idempotency.
- [x] Add tests for maintenance-first source seed creation.

## Slice 3: Maintenance Patch Models

**Goal:** create the typed contract between proposal, validation, and apply.

- [x] Add `MaintenancePatch`.
- [x] Add `MaintenancePatchOperation`.
- [x] Add high-level maintenance intent kinds such as:
  `seed_document`, `split_node`, `merge_nodes`, `correct_fact`,
  `derive_summary`, `derive_entity`, `derive_crosslink_candidate`,
  `add_crosslink`, `retract_crosslink`, `refresh_summary`,
  `promote_candidate`, `retract_promotion`, `distill_to_wisdom`,
  `request_review`.
- [x] Add low-level operation kinds:
  `ADD_NODE`, `ADD_EDGE`, `TOMBSTONE_NODE`, `TOMBSTONE_EDGE`,
  `REQUEST_REVIEW`, `NOOP`.
- [x] Document the intent-to-operation lowering rules:
  split adds nodes/edges; merge adds replacement nodes/edges and tombstones
  superseded ones; correction tombstones wrong primitives and adds corrected
  replacements; relink tombstones an old edge and adds a new edge; derivation
  adds derived nodes/edges with provenance; promotion adds or tombstones
  status/scope edges, or tombstones/adds replacement nodes when status is part
  of node identity.
- [x] Add stable `patch_id` and `operation_id`.
- [x] Require provenance fields on each graph-changing operation:
  `source_document_id`, `source_span_ids` or equivalent span pointers,
  `maintenance_run_id`, `confidence`.
- [x] Add patch status:
  `proposed`, `validated`, `partially_accepted`, `applied`, `rejected`,
  `needs_review`, `retracted`.
- [x] Keep LLM-facing patch proposal schemas strict and JSON-safe.

## Slice 4: Patch Validation

**Goal:** keep graph writes deterministic and accountable.

- [x] Validate source grounding for every node/edge operation.
- [x] Validate namespace scope.
- [x] Validate operation idempotency.
- [x] Validate edge endpoint existence or same-patch creation.
- [x] Validate tombstone operations target existing active facts.
- [x] Validate replacement/supersession lineage.
- [x] Reject graph-changing operations with missing provenance unless explicitly marked
  `REQUEST_REVIEW`.
- [x] Add tests for invalid grounding, missing endpoints, duplicate operations,
  and cross-namespace writes.

## Slice 5: Patch Application

**Goal:** apply accepted operations as a coherent graph update.

- [x] Reuse `kogwistar` transaction/runtime primitives where possible.
- [x] Apply a patch as one unit when backend transaction mode supports it.
- [x] Make patch application idempotent for retry.
- [x] Emit patch-applied artifacts.
- [x] Emit patch-failed artifacts with validation details.
- [x] Preserve operation-level status for partial acceptance.
- [x] Add tests for retrying the same patch without duplicate graph objects.

## Slice 6: Maintenance Job Taxonomy

**Goal:** expand beyond current `distill` and `execution_wisdom` jobs.

- [x] Add `document_seed_graph`.
- [x] Add `document_expand_parse_children`.
- [x] Add `document_correct_parse_children`.
- [x] Add `document_summarize_units`.
- [x] Add `document_extract_entities`.
- [x] Add `document_propose_crosslinks`.
- [x] Add `document_validate_crosslinks`.
- [x] Add `document_retract_crosslinks`.
- [x] Add `document_detect_conflicts`.
- [x] Add `conversation_promote_to_kg`.
- [x] Add `graph_patch_review`.
- [x] Add `graph_patch_apply`.
- [x] Route job kinds to workflow ids in `maintenance_policy.py`.
- [x] Ensure job kinds compile into the low-level add/tombstone patch
  vocabulary before application.

## Slice 7: Worker Strategy Layer

**Goal:** avoid turning `MaintenanceWorker` into one large switch.

- [x] Add a maintenance strategy registry.
- [x] Register current derived-knowledge and execution-wisdom strategies.
- [x] Add graph-patch proposal strategy.
- [x] Add graph-patch apply strategy.
- [x] Keep `MaintenanceDaemon` unchanged except for configuration metadata.
- [x] Add tests for strategy selection by maintenance kind.

## Slice 8: Cross-Link Maintenance

**Goal:** make links between documents candidate-first and retractable.

- [x] Add cross-link candidate patch operation.
- [x] Validate candidate links against source evidence from both sides.
- [x] Separate candidate cross-links from accepted cross-links.
- [x] Tombstone stale or incorrect cross-link edges instead of removing them.
- [x] Add confidence/review thresholds.
- [x] Add tests for add, reject, and retract cross-link flows.

## Slice 9: Conversation-Scoped Maintenance

**Goal:** support temporary thread-level graph construction.

- [x] Add conversation/thread maintenance scope.
- [x] Keep conversation-scoped facts out of workspace KG until promoted.
- [x] Add candidate promotion patches from conversation scope to workspace KG.
- [x] Preserve provenance back to conversation messages and source spans.
- [x] Add tests proving conversation scope does not leak into workspace KG
  before promotion.

## Slice 10: Status And Reporting

**Goal:** make eventual construction visible.

- [x] Track document graph status:
  `seeded`, `expanding`, `needs_review`, `stable`, `stale`, `superseded`.
- [x] Include graph status in ingest result.
- [x] Include patch counts in maintenance replies.
- [x] Add report fields for proposed/applied/rejected/retracted operations.
- [x] Add cost summaries by maintenance kind and model.
- [x] Add review query support for patch artifacts.

## Slice 11: Migration

**Goal:** introduce the mode without breaking parse-first workflows.

- [x] Keep `parse_first` as default.
- [x] Add `maintenance_first` behind explicit flag/config.
- [x] Add `hybrid` after source seed and patch application tests are stable.
- [x] Run the same document through all modes and compare:
  initial latency, accepted patch count, fallback rate, graph quality, and
  total model cost.
- [x] Promote `hybrid` only if it improves local-model reliability.

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
- Keep low-level graph persistence append-only: corrections are tombstone plus
  add, never in-place update or true removal.
- Do not add standalone evidence attachment operations; provenance belongs in
  each graph-changing operation.
