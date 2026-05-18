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

- Raw source documents are written to the foreground conversation namespace.
- Parsed document graph payloads are written to the foreground conversation
  namespace.
- Review-like artifacts such as candidate links, evidence packs, and promotion
  candidates are written to the background conversation namespace.
- Promoted knowledge is written to the current `kg` namespace.
- Demo KG mirroring writes parsed semantic-tree content into KG as a shortcut.
- `kg` is ambiguous and should become a legacy alias rather than a preferred
  semantic term.

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
- [x] Preserve existing properties as compatibility aliases:
  `conv_fg`, `conv_bg`, `kg`, `derived_knowledge`, `workflow_maintenance`,
  `review`, `projection_jobs`, and `maintenance_jobs`.
- [x] Prefer new names such as `curated_kg` over ambiguous `kg`.
- [x] Add helper metadata fields such as:
  `workspace_id`, `graph_space`, `graph_lane`, and `legacy_namespace`.
- [x] Add invariant helper that can validate namespace/metadata agreement.

Acceptance criteria:

- [x] Existing namespace tests still pass unchanged.
- [x] New tests prove graph-space namespace strings are deterministic.
- [x] New tests prove old aliases still resolve.
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

## Phase 4: Base KG Projection

**Goal:** add automatically extracted but unverified knowledge as `BASE_KG`.

- [ ] Add a configurable source-to-base extraction path.
- [ ] Support inputs from page-index parsing, iterative layerwise workflow, or
  user-provided pre-parsed content.
- [ ] Mark base facts/entities/relations as:
  `source_extracted`, `machine_extracted`, and `unverified`.
- [ ] Link every base fact/entity/relation back to `SOURCE` evidence.
- [ ] Do not write base extraction outputs into `CURATED_KG`.
- [ ] Do not call this step promotion.

Acceptance criteria:

- [ ] Source-extracted fact goes to `BASE_KG`, not `CURATED_KG`.
- [ ] Base KG result is queryable immediately.
- [ ] Base KG result carries source provenance.
- [ ] Curated KG remains clean unless promotion occurs.

## Phase 5: Curated KG Promotion Cleanup

**Goal:** make promotion mean accepted curated knowledge only.

- [ ] Rename app-facing semantics from `kg` to `curated_kg`.
- [ ] Keep `kg` as a legacy alias during migration.
- [ ] Ensure `promote_to_knowledge(...)` writes to `CURATED_KG`.
- [ ] Ensure promoted artifacts include review/evidence/source provenance.
- [ ] Ensure promotion does not move or mutate SOURCE or BASE_KG nodes.
- [ ] Update projection to read from `CURATED_KG` instead of ambiguous `kg`.

Acceptance criteria:

- [ ] Promoted conclusion goes to `CURATED_KG`.
- [ ] Source and base nodes remain in their original graph spaces.
- [ ] Projection reads curated/promoted state only unless explicitly configured
  otherwise.
- [ ] Legacy `kg` tests either pass through aliasing or are updated with
  explicit compatibility notes.

## Phase 6: Review Graph Cleanup

**Goal:** move review lifecycle artifacts out of background conversation.

- [ ] Write promotion candidates to `REVIEW`.
- [ ] Write evidence packs to `REVIEW`.
- [ ] Write approval/rejection decisions to `REVIEW`.
- [ ] Keep legacy `conv_bg` lookup fallback during migration if necessary.
- [ ] Ensure review artifacts link back to SOURCE, BASE_KG, or CURATED_KG
  evidence.

Acceptance criteria:

- [ ] Review artifacts are queryable from `REVIEW`.
- [ ] Conversation background contains lane messages, not the only durable
  review record.
- [ ] Promotion can find evidence packs from `REVIEW`.
- [ ] Long-run promotion provenance checks still pass.

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
- [ ] Document legacy aliases and migration behavior.
- [ ] Add diagrams showing SOURCE, BASE_KG, CURATED_KG, REVIEW, CONVERSATION,
  WORKFLOW, WISDOM, POLICY, and PROJECTION.

Acceptance criteria:

- [ ] Docs consistently use `curated_kg` where accepted/promoted knowledge is
  meant.
- [ ] Docs consistently call SOURCE/BASE_KG/CURATED_KG graph spaces, not engine
  graph types.
- [ ] Migration notes explain which old namespace strings remain aliases.

## Tests To Add

- [ ] Namespace builder tests for every graph space.
- [ ] Namespace/metadata agreement invariant tests.
- [ ] Legacy alias compatibility tests.
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

## Open Questions

- [ ] Should SOURCE/BASE_KG/CURATED_KG initially share the existing knowledge
  engine with separate namespaces, or should SOURCE receive a dedicated engine
  later?
- [ ] Should workspace-style query include WISDOM by default, or only when
  explicitly requested?
- [ ] What is the first BASE_KG extractor policy: deterministic only, LLM-backed,
  user-provided pre-parsed, or configurable per ingest request?
- [ ] What metadata field name should be canonical: `graph_space`,
  `semantic_layer`, or another term?
- [ ] When can legacy `kg` naming be removed from user-facing APIs?
