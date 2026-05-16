# Promotion Evidence Pack Provenance Checklist

## Summary

Promotion provenance is a hard `kogwistar` semantic.

Any promoted knowledge must point to a durable evidence/provenance pack that identifies the exact source nodes and edges used to justify promotion.

`kogwistar` owns the reusable evidence-pack primitive. `kogwistar-llm-wiki` owns how its ingest pipeline builds and attaches promotion evidence.

## Current Status

This checklist is now complete.

The core evidence-pack primitive, production `llm-wiki` ingest promotion path,
derivation provenance walk tests, and the long-run harness promotion provenance
checks are all wired through the same `promotion_evidence_pack` contract.

## Core Semantics

- [x] Add a reusable core evidence-pack model outside conversation-specific code, for example `kogwistar.provenance.EvidencePackDigest`.
- [x] Keep the shape compatible with the current conversation evidence pack:
  - `node_ids: list[str]`
  - `edge_ids: list[str]`
  - `depth: str`
  - `max_chars_per_item: int`
  - `max_total_chars: int`
  - `evidence_pack_hash: str | None`
  - extra workflow-specific metadata allowed.
- [x] Re-export or adapt the existing conversation `EvidencePackDigest` so conversation answering and llm-wiki promotion share the same primitive semantics.
- [x] Add a core helper to compute a deterministic digest/hash from canonical evidence-pack content.
- [x] Add core docs/tests stating:
  - promoted or derived artifacts must keep `node_ids` separate from `edge_ids`
  - readers must not infer ID kind from a mixed list
  - an evidence pack is generic; promotion is one use case.

## llm-wiki Promotion

- [x] Add a promotion evidence-pack creation step between parsed graph persistence and `create_promotion_candidate(...)`.
- [x] Build the pack from the actual parsed graph extraction used for ingest:
  - include `source_document_id`
  - include all persisted parsed node IDs from `graph_extraction.nodes`
  - include all persisted parsed edge IDs from `graph_extraction.edges`
  - include `candidate_link_id` only as workflow lineage metadata, not as raw evidence.
- [x] Persist a durable `promotion_evidence_pack` artifact node in the background conversation namespace.
- [x] Store these fields on the evidence-pack node metadata:
  - `artifact_kind="promotion_evidence_pack"`
  - `workspace_id`
  - `source_document_id`
  - `node_ids`
  - `edge_ids`
  - `evidence_pack_hash`
  - `evidence_role="promotion"`
  - `created_from="parsed_graph_extraction"`
- [x] Update `promotion_candidate` metadata to reference the evidence pack:
  - `promotion_evidence_pack_id`
  - `promotion_evidence_pack_digest`
  - keep `candidate_link_id`
  - replace or supplement loose `lineage_source_ids` with typed fields: `lineage_node_ids` and `lineage_edge_ids`.
- [x] Update `promoted_knowledge` metadata to reference the same pack:
  - `promotion_candidate_id`
  - `promotion_evidence_pack_id`
  - `promotion_evidence_pack_digest`
  - `promotion_decision_reason`
  - `promotion_decision_metadata`
- [x] Keep IDs deterministic:
  - evidence pack ID stable from `workspace_id + source_document_id + sorted node_ids + sorted edge_ids`
  - same parsed evidence replay reuses the same pack.
- [x] Keep `candidate_link` as an artifact node. Do not treat it as a hyperedge.

Note: these checked items apply to the production ingest pipeline path. The
long-run workflow harness still needs to be updated to call the same evidence
pack path.

## Derivation And Maintenance

- [x] Confirm `derived_knowledge` continues to point to promoted knowledge through `source_node_ids`.
- [x] Ensure the derivation chain remains auditable:
  - `derived_knowledge.source_node_ids` are promoted knowledge node IDs
  - each promoted source has a `promotion_evidence_pack_id`.
- [x] Do not require derived knowledge to duplicate every raw parsed node/edge when it can reach them through promoted source nodes.
- [x] Add a regression test that walks:
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

- [x] Core test: evidence-pack digest keeps node IDs and edge IDs separate and hashes deterministically.
- [x] Ingest test: `sync` promotion writes a `promotion_evidence_pack` node.
- [x] Ingest test: promoted knowledge metadata references the evidence pack and promotion candidate.
- [x] Ingest test: promotion candidate metadata references the same evidence pack and keeps typed lineage fields.
- [x] Replay test: running the same ingest twice does not create duplicate promotion evidence packs.
- [x] Provenance test: every promoted node can be traced to all parsed graph node IDs and edge IDs used for promotion.
- [x] Derivation test: derived knowledge can be traced through promoted knowledge to the original promotion evidence pack.
- [x] Long-run test update: dump exporter includes promotion evidence-pack nodes and reports missing packs as an invariant failure.

## Acceptance Criteria

- [x] A retrieved `promoted_knowledge` node can answer:
  - what source document it came from
  - which promotion candidate approved it
  - which evidence pack justified it
  - which exact parsed nodes and edges were used
  - why the policy allowed promotion.
- [x] No code path relies on mixed `lineage_source_ids` to guess whether an ID names a node or edge.
- [x] Conversation answering and llm-wiki promotion share the same evidence-pack concept, even though they use it for different workflow semantics.
- [x] Existing promotion behavior remains unchanged except for stronger provenance metadata and durable evidence-pack artifacts.
