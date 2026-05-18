# Graph Space Namespace Refactor Checklist

## Summary

Refactor `kogwistar-llm-wiki` namespace and ingestion semantics so source
documents, base extracted knowledge, curated knowledge, review artifacts,
workflow state, conversations, wisdom, policy, and projections are no longer
overloaded into conversation or ambiguous `kg` namespaces.

The key semantic correction is:

- `source`, `base_kg`, `curated_kg`, `review`, `policy`, and `projection` are
  **graph spaces / semantic layers**, not Kogwistar engine graph kinds.
- Kogwistar engine graph kind remains broad, such as `knowledge`,
  `conversation`, `workflow`, and `wisdom`.
- Namespace is an operational routing/replay/storage partition.
- Metadata is the inspectable semantic record.
- Namespace and metadata should agree.

## Current State

- Raw source documents are written to SOURCE, with a temporary foreground
  conversation copy for compatibility.
- Parsed document graph payloads are written to SOURCE, with a temporary
  foreground conversation copy for compatibility.
- BASE_KG is populated as explicit reference artifacts that point back to
  SOURCE.
- Review-like artifacts such as candidate links, evidence packs, and promotion
  candidates are written to the background conversation namespace.
- Promoted knowledge is written to CURATED_KG.
- Demo curated mirroring still writes parsed semantic-tree content as a
  shortcut.
- Legacy `kg` namespace naming has been removed from app graph-space routing.

## Target Graph Spaces

- `SOURCE`: authoritative source/evidence layer.
- `BASE_KG`: machine/source-extracted knowledge, queryable but unverified.
- `CURATED_KG`: accepted/promoted stable knowledge.
- `CONVERSATION`: foreground/background interaction and lane memory.
- `WORKFLOW`: workflow runs, jobs, traces, and execution state.
- `REVIEW`: promotion candidates, evidence packs, approvals, rejections, and
  audit decisions.
- `WISDOM`: distilled reusable lessons, heuristics, and synthesis.
- `POLICY`: ACL, capability, governance, quota, and approval semantics.
- `PROJECTION`: rebuildable external/materialized views.

## Non-Goals

- [ ] Do not treat graph spaces as Kogwistar engine graph kinds.
- [ ] Do not move page-index parser logic into Kogwistar core.
- [ ] Do not blindly promote all parsed source into curated knowledge.
- [ ] Do not call source ingestion or base extraction "promotion".
- [ ] Do not physically split databases/backends before logical semantics are
  stable.
- [ ] Do not remove legacy namespace aliases in the first slice.

## Invariants

- [ ] `workspace_id` is app/project scope, not workflow and not graph space.
- [ ] Engine graph kind is broad storage/runtime family, not graph space.
- [ ] Namespace routes data; metadata explains data.
- [ ] Namespace graph-space segment and metadata graph-space field must agree.
- [ ] `SOURCE` is queryable before promotion.
- [ ] `BASE_KG` is distinguishable from `CURATED_KG`.
- [ ] `CURATED_KG` contains accepted/promoted knowledge only.
- [ ] `CONVERSATION` is not the source-document store.
- [ ] `REVIEW` artifacts are not hidden only in background conversation.
- [ ] Projection is rebuildable and not authoritative.
- [ ] Demo paths must obey the same graph-space invariants as normal paths.

## Phase 1: Namespace Builder And Vocabulary

**Goal:** add explicit graph-space vocabulary without changing storage behavior.

- [x] Add a `GraphSpace` enum or equivalent app-level constants in
  `kogwistar-llm-wiki`.
- [x] Add typed namespace builder helpers for:
  `source`, `base_kg`, `curated_kg`, `conversation`, `workflow`, `review`,
  `wisdom`, `policy`, and `projection`.
- [x] Preserve existing non-KG properties as compatibility aliases:
  `conv_fg`, `conv_bg`, `derived_knowledge`, `workflow_maintenance`,
  `review`, `projection_jobs`, and `maintenance_jobs`.
- [x] Prefer new names such as `curated_kg` over ambiguous `kg`.
- [x] Add helper metadata fields such as:
  `workspace_id`, `graph_space`, `graph_lane`, and `legacy_namespace`.
- [x] Add invariant helper that can validate namespace/metadata agreement.

Acceptance criteria:

- [x] Existing namespace tests still pass unchanged.
- [x] New tests prove graph-space namespace strings are deterministic.
- [x] New tests prove retained aliases still resolve.
- [x] New tests prove namespace/metadata mismatch is detectable.
- [x] No ingestion behavior changes in this phase.

## Phase 2: Source Graph Write Path

**Goal:** make parsed source documents directly queryable in `SOURCE`.

- [x] Write raw source documents to `SOURCE`.
- [x] Write parsed document graph payloads to `SOURCE`.
- [x] Keep legacy foreground conversation write temporarily if needed for
  compatibility.
- [x] Add `graph_space="source"` metadata to source documents, nodes, and edges.
- [x] Ensure deterministic parser provenance and span grounding survive the
  write path.
- [x] Update long-run artifacts/reporting to show source graph writes
  separately from conversation lane writes.

Acceptance criteria:

- [x] Parsed document is queryable from `SOURCE` before promotion.
- [x] Parsed document is not required to exist in `CURATED_KG`.
- [x] Existing long-run ingestion tests continue passing.
- [x] Source spans remain grounded and validate against source text.
- [x] No source document is stored only in conversation.

## Phase 3: Query Graph-Space Selection

**Goal:** make retrieval choose graph spaces explicitly.

- [x] Add an app-level query helper that accepts explicit graph spaces.
- [x] Support `graph_spaces=["source"]`.
- [x] Support `graph_spaces=["base_kg"]` after Phase 4 exists.
- [x] Support `graph_spaces=["curated_kg"]`.
- [x] Support `graph_spaces=["source", "base_kg", "curated_kg"]` for
  workspace-style search.
- [x] Keep `workspace` as a convenience query preset only, not a graph space.
- [x] Return graph-space metadata with each result.

Acceptance criteria:

- [x] Querying `SOURCE` returns parsed document content before promotion.
- [x] Querying `CURATED_KG` excludes raw parsed source.
- [x] Workspace-style search expands to explicit graph spaces.
- [x] Results expose graph space so callers can explain where answers came
  from.

## Phase 4: Base KG Reference Projection

**Goal:** add an explicit reference layer in `BASE_KG` that points back to
`SOURCE` instead of duplicating source truth.

- [x] Add a configurable source-to-base reference projection path.
- [x] Support inputs from page-index parsing, iterative layerwise workflow, or
  user-provided pre-parsed content.
- [x] Mark base references as `source_referenced`, `machine_extracted`, and
  `unverified`.
- [x] Link every base reference back to `SOURCE` evidence via explicit pointer
  artifacts.
- [x] Do not write base projection outputs into `CURATED_KG`.
- [x] Do not call this step promotion.

Acceptance criteria:

- [x] Source-extracted fact goes to `BASE_KG`, not `CURATED_KG`.
- [x] Base KG result is queryable immediately.
- [x] Base KG result carries source provenance.
- [x] Curated KG remains clean unless promotion occurs.

## Phase 5: Curated KG Promotion Cleanup

**Goal:** make promotion mean accepted curated knowledge only.

- [x] Rename app-facing semantics from `kg` to `curated_kg`.
- [x] Remove `kg` as a legacy alias from app graph-space routing.
- [x] Ensure `promote_to_knowledge(...)` writes to `CURATED_KG`.
- [x] Ensure promoted artifacts include review/evidence/source provenance.
- [x] Ensure promotion does not move or mutate SOURCE or BASE_KG nodes.
- [x] Update projection to read from `CURATED_KG` instead of ambiguous `kg`.

Acceptance criteria:

- [x] Promoted conclusion goes to `CURATED_KG`.
- [x] Source and base nodes remain in their original graph spaces.
- [x] Projection reads curated/promoted state only unless explicitly configured
  otherwise.
- [x] Legacy `kg` alias tests are replaced with explicit rejection or
  curated-space assertions.

## Phase 6: Review Query Helper Only

**Goal:** keep review artifacts in background conversation, but make review
lookup explicit and low-friction.

- [x] Add an app-level helper for review artifact queries in `conv_bg`.
- [x] Expose convenience wrappers for candidate links, promotion candidates,
  evidence packs, and review-chain lookup.
- [x] Keep review lookup based on existing metadata links and `artifact_kind`.
- [x] Avoid introducing a new `REVIEW` graph space or new review taxonomy
  fields in this phase.
- [x] Do not change write paths, promotion flow, or long-run artifact
  placement.

Acceptance criteria:

- [x] Review artifacts are queryable through a helper, not only by ad hoc
  conversation scans.
- [x] Helper results stay workspace-scoped and exclude ordinary lane messages.
- [x] Review-chain lookup can resolve promoted node -> candidate -> evidence
  pack -> evidence ids.
- [x] Existing promotion provenance checks still pass.

## Phase 7: Demo Path Migration

**Goal:** remove demo-only violations of graph-space invariants.

- [ ] Stop mirroring parsed source graph directly into curated KG.
- [ ] Write demo parsed source graph to `SOURCE`.
- [ ] Optionally run BASE_KG extraction if demo configuration requests it.
- [ ] Show demo graph output through explicit graph-space query/projection
  choices.

Acceptance criteria:

- [ ] Demo no longer places raw parsed source into `CURATED_KG`.
- [ ] Demo still shows useful queryable content via `SOURCE` or `BASE_KG`.
- [ ] Demo and normal ingestion obey the same graph-space invariants.

## Phase 8: Policy And ACL Alignment

**Goal:** keep owner, principal, and security scope distinct from graph space.

- [ ] Avoid adding `owner_type` / `owner_id` to namespace strings unless an ACL
  design explicitly requires it.
- [ ] Store actor fields with precise names such as `principal`, `subject`,
  `created_by`, or `service_account_id`.
- [ ] Keep `security_scope`, `storage namespace`, and `execution_namespace`
  distinct.
- [ ] Add policy tests for graph-space visibility and read eligibility.

Acceptance criteria:

- [ ] Workspace scope is not used as a substitute for ACL.
- [ ] Graph-space routing is not used as the only visibility check.
- [ ] User-facing retrieval respects policy before summarization/ranking.

## Phase 9: Documentation And Migration Notes

**Goal:** keep terminology and compatibility clear while implementation changes.

- [x] Update glossary to distinguish graph space from engine graph kind.
- [ ] Update lane namespace convention to avoid implying source documents belong
  in conversation.
- [ ] Update ADRs/checklists that describe `kg` as the only knowledge graph.
- [x] Document resolved graph-space decisions and removed `kg` compatibility.
- [ ] Add diagrams showing SOURCE, BASE_KG, CURATED_KG, REVIEW, CONVERSATION,
  WORKFLOW, WISDOM, POLICY, and PROJECTION.

Acceptance criteria:

- [ ] Docs consistently use `curated_kg` where accepted/promoted knowledge is
  meant.
- [ ] Docs consistently call SOURCE/BASE_KG/CURATED_KG graph spaces, not engine
  graph types.
- [x] Migration notes explain that `kg` is no longer a graph-space alias.

## Tests To Add

- [ ] Namespace builder tests for every graph space.
- [ ] Namespace/metadata agreement invariant tests.
- [x] `kg` alias rejection tests.
- [ ] Parsed document is written to SOURCE.
- [ ] Parsed document remains queryable before promotion.
- [ ] Parsed document is not written to CURATED_KG by default.
- [ ] Source-extracted fact goes to BASE_KG, not CURATED_KG.
- [ ] Promoted conclusion goes to CURATED_KG, not SOURCE or BASE_KG.
- [ ] Review artifacts go to REVIEW.
- [ ] Conversation messages stay in CONVERSATION.
- [ ] Workflow jobs/traces stay in WORKFLOW/job namespaces.
- [ ] Projection reads curated/promoted state by default.
- [ ] Demo path obeys source/base/curated separation.
- [ ] Query helper can search explicit graph-space lists.

## Resolved Decisions

- [x] SOURCE, BASE_KG, and CURATED_KG initially share the existing knowledge
  engine and are separated by explicit graph-space namespaces.
- [x] Workspace-style query defaults to SOURCE and CURATED_KG. WISDOM is
  opt-in through `include_wisdom=True`.
- [x] The first BASE_KG policy is deterministic source-reference projection from
  parsed graph outputs. It does not run a separate LLM extraction step.
- [x] `graph_space` is the canonical metadata field for the application graph
  space.
- [x] Legacy `kg` naming can be removed from user-facing APIs and graph-space
  compatibility aliases immediately.
