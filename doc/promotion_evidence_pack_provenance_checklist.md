# Promotion Evidence Pack Provenance Checklist

## Summary

Promotion provenance is a hard `kogwistar` semantic.

Any promoted knowledge must point to a durable evidence/provenance pack that identifies the exact source nodes and edges used to justify promotion.

`kogwistar` owns the reusable evidence-pack primitive. `kogwistar-llm-wiki` owns how its ingest pipeline builds and attaches promotion evidence.

## Core Semantics

- [ ] Add a reusable core evidence-pack model outside conversation-specific code, for example `kogwistar.provenance.EvidencePackDigest`.
- [ ] Keep the shape compatible with the current conversation evidence pack:
  - `node_ids: list[str]`
  - `edge_ids: list[str]`
  - `depth: str`
  - `max_chars_per_item: int`
  - `max_total_chars: int`
  - `evidence_pack_hash: str | None`
  - extra workflow-specific metadata allowed.
- [ ] Re-export or adapt the existing conversation `EvidencePackDigest` so conversation answering and llm-wiki promotion share the same primitive semantics.
- [ ] Add a core helper to compute a deterministic digest/hash from canonical evidence-pack content.
- [ ] Add core docs/tests stating:
  - promoted or derived artifacts must keep `node_ids` separate from `edge_ids`
  - readers must not infer ID kind from a mixed list
  - an evidence pack is generic; promotion is one use case.

## llm-wiki Promotion

- [ ] Add a promotion evidence-pack creation step between parsed graph persistence and `create_promotion_candidate(...)`.
- [ ] Build the pack from the actual parsed graph extraction used for ingest:
  - include `source_document_id`
  - include all persisted parsed node IDs from `graph_extraction.nodes`
  - include all persisted parsed edge IDs from `graph_extraction.edges`
  - include `candidate_link_id` only as workflow lineage metadata, not as raw evidence.
- [ ] Persist a durable `promotion_evidence_pack` artifact node in the background conversation namespace.
- [ ] Store these fields on the evidence-pack node metadata:
  - `artifact_kind="promotion_evidence_pack"`
  - `workspace_id`
  - `source_document_id`
  - `node_ids`
  - `edge_ids`
  - `evidence_pack_hash`
  - `evidence_role="promotion"`
  - `created_from="parsed_graph_extraction"`
- [ ] Update `promotion_candidate` metadata to reference the evidence pack:
  - `promotion_evidence_pack_id`
  - `promotion_evidence_pack_digest`
  - keep `candidate_link_id`
  - replace or supplement loose `lineage_source_ids` with typed fields: `lineage_node_ids` and `lineage_edge_ids`.
- [ ] Update `promoted_knowledge` metadata to reference the same pack:
  - `promotion_candidate_id`
  - `promotion_evidence_pack_id`
  - `promotion_evidence_pack_digest`
  - `promotion_decision_reason`
  - `promotion_decision_metadata`
- [ ] Keep IDs deterministic:
  - evidence pack ID stable from `workspace_id + source_document_id + sorted node_ids + sorted edge_ids`
  - same parsed evidence replay reuses the same pack.
- [ ] Keep `candidate_link` as an artifact node. Do not treat it as a hyperedge.

## Derivation And Maintenance

- [ ] Confirm `derived_knowledge` continues to point to promoted knowledge through `source_node_ids`.
- [ ] Ensure the derivation chain remains auditable:
  - `derived_knowledge.source_node_ids` are promoted knowledge node IDs
  - each promoted source has a `promotion_evidence_pack_id`.
- [ ] Do not require derived knowledge to duplicate every raw parsed node/edge when it can reach them through promoted source nodes.
- [ ] Add a regression test that walks:
  - `derived_knowledge.source_node_ids`
  - to `promoted_knowledge`
  - to `promotion_evidence_pack_id`
  - to raw parsed `node_ids` and `edge_ids`.

## Docs And Diagrams

- [x] Update `doc/diagrams.md` so the promotion lineage diagram says:
  - `candidate_link` is a node, not a hyperedge
  - promotion evidence pack records typed `node_ids` and `edge_ids`
  - promoted knowledge points to the evidence pack
  - derived knowledge points to promoted knowledge, then indirectly to the evidence pack.
- [x] Add a short example showing one promoted node and the fields a reader follows.
- [x] Update `doc/longrun_workflow_test.md` so the long-run dump must include promotion evidence-pack records.
- [x] Add a note that losing typed promotion evidence is a correctness bug, not just missing metadata.

## Tests

- [ ] Core test: evidence-pack digest keeps node IDs and edge IDs separate and hashes deterministically.
- [ ] Ingest test: `sync` promotion writes a `promotion_evidence_pack` node.
- [ ] Ingest test: promoted knowledge metadata references the evidence pack and promotion candidate.
- [ ] Ingest test: promotion candidate metadata references the same evidence pack and keeps typed lineage fields.
- [ ] Replay test: running the same ingest twice does not create duplicate promotion evidence packs.
- [ ] Provenance test: every promoted node can be traced to all parsed graph node IDs and edge IDs used for promotion.
- [ ] Derivation test: derived knowledge can be traced through promoted knowledge to the original promotion evidence pack.
- [ ] Long-run test update: dump exporter includes promotion evidence-pack nodes and reports missing packs as an invariant failure.

## Acceptance Criteria

- [ ] A retrieved `promoted_knowledge` node can answer:
  - what source document it came from
  - which promotion candidate approved it
  - which evidence pack justified it
  - which exact parsed nodes and edges were used
  - why the policy allowed promotion.
- [ ] No code path relies on mixed `lineage_source_ids` to guess whether an ID names a node or edge.
- [ ] Conversation answering and llm-wiki promotion share the same evidence-pack concept, even though they use it for different workflow semantics.
- [ ] Existing promotion behavior remains unchanged except for stronger provenance metadata and durable evidence-pack artifacts.
