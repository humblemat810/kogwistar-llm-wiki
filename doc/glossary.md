# LLM-Wiki Glossary (Kogwistar-based)

This glossary defines key terminology used in the system.  
It focuses on **commonly confused concepts** and establishes **clear boundaries**.

---

## 1. Core Concepts

### Graph
A set of nodes and edges stored in Kogwistar as an **event-sourced hypergraph**.

> There is only **one underlying graph substrate**.

---

### Graph Space (a.k.a. Namespace / Collection)
A logical grouping of nodes and edges with a shared purpose.

Examples:
- Conversation Graph
- Knowledge Graph (KG)
- Maintenance Graph
- Wisdom Graph

> These are **application-level partitions**, not engine-level graph types.

---

### Node
A unit of information in the graph.

Examples:
- message
- entity
- concept
- summary
- workflow run
- wisdom insight

---

### Edge
A relationship between nodes.

Examples:
- `mentions`
- `derived_from`
- `supports`
- `contradicts`
- `proposes_link`

> Edge types should express **semantic meaning**, not workflow stage.

---

## 2. Identity & Determinism

### Stable Identity
A deterministic identifier (e.g. uuid5-based) used to ensure:

- idempotency
- replay stability
- deduplication

> Identity is **machine-oriented**, not necessarily human-readable.

---

### Deterministic Artifact
An artifact whose identity is derived from:

- its inputs
- its pipeline version
- its context

---

### Fragment
A structured portion of a document (e.g. section, paragraph, table cell).

> Fragment is a **semantic role**, not an ID type.

---

### Derived Artifact
An artifact produced from other artifacts (e.g. summary, link, cluster).

> “Derived” is expressed via **provenance and relations**, not ID prefixes.

---

## 3. Grounding & Provenance

### Span
The smallest addressable unit of evidence.

Examples:
- text range
- bounding box (OCR)
- table cell

---

### Grounding
A set of one or more spans that support an artifact.

> Grounding answers:  
> **“Where exactly does this come from?”**

---

### Provenance
The full lineage of an artifact:

- inputs
- transformations
- workflow steps

> Provenance answers:  
> **“How was this created?”**

---

## 4. Graph Spaces

### Conversation Graph
Working memory.

Contains:
- user messages
- assistant responses
- intermediate reasoning
- temporary artifacts

Characteristics:
- high churn
- not authoritative

---

### Knowledge Graph (KG)
Durable, promoted knowledge.

Contains:
- entities
- validated relationships
- stable facts

Characteristics:
- authoritative
- projected to Obsidian

---

### Maintenance Graph
System reasoning layer.

Contains:
- candidate links
- merge proposals
- contradiction signals
- clustering outputs

Characteristics:
- ephemeral
- revisable
- not projected

---

### Wisdom Graph
Reusable, execution-derived knowledge.

Contains:
- patterns
- strategies
- lessons learned

Characteristics:
- derived from execution
- cross-context reusable
- complements KG

---

## 5. Lifecycle Concepts

### Promotion
The act of creating a **new authoritative artifact in the Knowledge Graph** based on evaluated inputs.

> Promotion does NOT move or mutate existing nodes.  
> It creates a new node/edge with provenance links.

---

### Promoted Artifact
A node or edge that exists in the Knowledge Graph after promotion.

---

### Candidate
An artifact not yet promoted.

Typically lives in:
- Conversation Graph
- Maintenance Graph

---

### Superseded
An artifact that has been replaced by a newer version.

---

### Tombstoned
An artifact that is logically deleted (but still exists in history).

---

## 6. Relation Semantics

### Relation Type
The semantic meaning of an edge.

Examples:
- `mentions`
- `derived_from`
- `supports`
- `contradicts`

> Should NOT encode:
- graph space
- workflow stage
- agent type

---

### Cross-Space Relation
A relation where endpoints belong to different graph spaces.

> Cross-space is **implicit**, not encoded in the relation type.

---

## 7. Pin / Ref / Projection

### Pin
A mechanism to **stabilize or anchor a reference** to an artifact across contexts.

---

### Ref
A reference from one node to another.

> Used for:
- linking
- reuse
- contextualization

---

### Projection
A derived external representation of the graph.

Examples:
- Obsidian markdown files

> Projection is:
- NOT authoritative
- rebuildable from the graph

---

## 8. Maintenance vs Reasoning vs Wisdom

### Maintenance
System processes that:

- evaluate candidates
- resolve conflicts
- improve structure

> Maintenance is **a process**, not a graph type.

---

### Reasoning
Temporary or intermediate thinking.

Lives in:
- Conversation Graph
- Maintenance Graph

---

### Wisdom
Generalized, reusable knowledge derived from execution.

> Wisdom answers:  
> **“What tends to work and why?”**

---

## 9. Key Invariants

- Graph is authoritative
- Events are append-only
- No in-place mutation
- Identity is deterministic
- Promotion creates new artifacts
- Grounding must be preserved
- Projection is derived, not primary
- Semantics belong in relations, not naming conventions

---

## 10. Common Confusions (Quick Reference)

| Confusion | Correct Understanding |
|----------|---------------------|
| fragment = ID type | ❌ No, it's a semantic role |
| derived = ID prefix | ❌ No, use provenance |
| cross-space = relation type | ❌ No, inferred from endpoints |
| promotion = update | ❌ No, it creates new artifact |
| maintenance = graph type | ❌ No, it's a process |
| wisdom = single node | ❌ No, it's a graph space |
| projection = source of truth | ❌ No, graph is authoritative |

---

END