# ADR: Invariant-Safe Layered Reparse

## Decision

Source bytes and parser derivations are immutable evidence. A logical
`source_document_id` identifies the source across revisions, while each
revision is stored under a deterministic revision document ID derived from
workspace, logical source, and revision identity. The logical document remains
only as a read-only compatibility alias for older callers; it is never updated
with later bytes.

Layered parsing state is application-owned and restartable. The parser returns
bounded, serializable seed and expansion DTOs; LLM-Wiki persists sessions and
frontiers in named projections and keeps temporary parser directories
disposable. A parse generation is immutable evidence. A per-source ParseView
is the only active-interpretation pointer.

ParseView activation uses a named projection key:

```text
workspace: ws:<workspace_id>:projection_state
key:       parse_view:<logical_source_document_id>
```

The view version is a per-source compare-and-swap token, not a workspace-wide
sequence. Readers resolve one active view for one logical source; historical
source and derivation inspection remains explicitly unfiltered.

## Backend Safety

Direct corrective patches that supersede an active node or edge require the
generic Kogwistar atomic-mutation capability. PostgreSQL declares this
capability and applies the complete patch inside its transaction. Chroma and
no-op backends are non-atomic: direct replacements are rejected before any
write and must use the ParseView activation path. A failed operation in an
atomic replacement raises and rolls back the patch; additive idempotent work
retains its existing retry behavior.

The Chroma cleanup writer may later tombstone inactive physical objects, but
cleanup cannot change the active ParseView and is never the visibility fence.

## Lifecycle

1. Capture the source revision and immutable revision document.
2. Seed a parse generation and one bounded frontier item.
3. Expand one bounded frontier batch, persist its commit/frontier state, and
   remain `parse_expanding` while work remains.
4. Activate a new per-source ParseView with CAS only after all selected regions
   are grounded in the same revision and are non-overlapping.
5. Mark `parsed_graph_persisted` only when the frontier is empty or the parser
   declares the session stable.

Legacy parse data is treated as virtual `G0` at read time. No destructive
backfill or corpus-wide model-upgrade reparse is implied.

## Data Flow

```text
immutable SOURCE revision/document
            |
            v
kg-doc-parser layered seed/expand contract
            |
            v
LLM-Wiki durable session + frontier projections
            |
            v
immutable generation + committed members
            |
            v
per-source ParseView CAS pointer
            |
            v
active query/projection/reconciliation reads
```

The parser contract supplies parsing mechanics and bounded DTOs. LLM-Wiki
supplies application policy, source-revision fences, persistence, and view
activation. Kogwistar supplies the durable queue and leases, named projection
CAS, namespace-scoped graph reads, spans, and backend atomicity capability.
These layers are deliberately not reimplemented in the application.

## Reuse Audit And Scope

| Concern | Existing primitive | Application responsibility |
| --- | --- | --- |
| Layered parsing | `kg_doc_parser.workflow_ingest.layered_contracts` | Adapt its bounded results to durable source-pinned sessions |
| Work scheduling | Kogwistar `JobQueueSubsystem` | Choose maintenance policy and coalesce follow-up jobs |
| Durable CAS state | Kogwistar named projections | Define parse-session, generation, and ParseView payload schemas |
| Graph traversal | Kogwistar `ReadSubsystem.get_nodes/get_edges` | Classify affected dependents; no parser-specific reverse index |
| Replacement safety | Kogwistar `AtomicMutationCapability` and `engine.uow()` | Reject non-atomic direct replacements and use ParseView for Chroma |
| Grounding | Kogwistar `Span` and source-pointer validation | Pin derivations to immutable revision documents and regions |

The application-owned schemas are not a second queue, transaction system, or
grounding model. They carry source and derivation identity needed to make the
existing generic primitives safe for selective parsing. The full provider,
PostgreSQL/Chroma crash, and GPU/model benchmark suites remain operational
validation rather than claims made by these provider-free tests.

## Invariants

- raw source bytes, user conversation facts, spans, and parse evidence are not
  edited in place;
- a maintenance correction is add-plus-tombstone with provenance, or a new
  ParseView, never a raw-fact overwrite;
- every session, generation, frontier item, and view is workspace-scoped;
- stale revision jobs are rejected before parser work;
- duplicate delivery is safe through deterministic generation-member event IDs
  and named-projection CAS;
- targeted derivations must keep every grounded span inside the requested
  immutable revision region;
- maintenance cannot bypass acceptance fences or create recursive maintenance
  jobs during execution.

## Repository Boundaries

- `kogwistar`: only generic backend atomic-capability reporting;
- `kg-doc-parser`: bounded serializable layered seed/expansion contracts;
- `kogwistar-llm-wiki`: revision documents, durable sessions/frontiers,
  ParseViews, orchestration, reconciliation, and operator-visible status.
