# ADR: Multimodal Evidence Reference Graph

## Status

Proposed implementation in the multimodal evidence feature branch.

## Decision

Kogwistar remains the owner of immutable evidence and generic graph-reference
contracts. LLM-Wiki remains the owner of embedding providers, vector indexes,
ACL-aware dereferencing, and application orchestration.

An embedding profile identifies a complete semantic space. Provider, model,
revision, preprocessing policy, embedding kind, metric, dimension, and patch
limits are part of that identity. Two profiles with the same dimension are not
interchangeable.

The projection flow is:

```mermaid
flowchart LR
    PROFILE[Complete embedding profile] --> PROJECTION[Profile-isolated vector\nreference projection]
    PROJECTION --> SPAN[Immutable MultimodalSpan]
    SPAN --> SOURCE[Pinned source-map target\nauthoritative evidence]
    SOURCE --> OPTIONAL[Optional semantic, conversation,\nnode, edge, or hyperedge target]
    PROFILE -. same dimension is insufficient .-> OTHER[Different model or preprocessing]
    OTHER -. rejected as a mismatch .-> PROJECTION
```

`MultimodalSpan` is source evidence, not a claim. A source revision and its
content hash are immutable. Text ranges, image regions, audio intervals, video
intervals, and per-frame video region tracks use typed locators. A video track
stores an immutable manifest reference and digest instead of an unbounded list
of frame boxes inside a graph entity.

An `EmbeddingReference` stores the profile fingerprint, embedding-set ID,
source span, and typed target references. It stores no vectors and no query
scores. A late-interaction set such as 200 ColQwen vectors therefore remains
one embedding set and one source reference, rather than becoming 200 graph
nodes.

The source-map target is authoritative and must be pinned. Semantic,
conversation, node, edge, and hyperedge targets are optional derived links.
Similarity alone never creates canonical graph truth.

## Compatibility And Safety

Existing text `Span` and `Grounding.spans` payloads remain valid. Grounding is
valid only when it contains at least one text or multimodal span. Flattened
payloads gain additive multimodal span tables and IDs; old payloads without
those fields continue to load.

LLM-Wiki validates workspace and namespace authorization before dereferencing
source-map or semantic targets. Stale and unauthorized references are returned
as labeled retrieval results. They are not silently redirected or rewritten.

Vector stores are profile-scoped and reject profile mismatches before writes or
queries. Canonical graph/source/conversation storage is independent of vector
projection lifetime.

```mermaid
sequenceDiagram
    participant Q as Query
    participant V as Profile-scoped vector store
    participant A as ACL and namespace guard
    participant G as Canonical graph
    Q->>V: Search with complete profile fingerprint
    V-->>Q: Candidate embedding references
    Q->>A: Authorize pinned source-map target
    A->>G: Resolve source and optional targets
    G-->>Q: Grounded or labeled stale result
    V-->>Q: Reject cross-profile read before scoring
```

## Consequences

- Core models can represent multimodal evidence without embedding-provider code.
- LLM-Wiki can support dense, audio/video, and late-interaction retrieval using
  the same reference shape.
- Existing source and graph invariants remain the authority for truth,
  provenance, namespaces, and ACLs.
- Legacy free-form locators remain readable but unsupported legacy forms must
  be converted before new indexing.
