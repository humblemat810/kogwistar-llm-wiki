# LLM-Wiki Glossary (Kogwistar-based)

This glossary defines key terminology used in the system. It focuses on
commonly confused concepts and establishes clear boundaries.

---

## 1. Core Concepts

### Graph

A set of nodes and edges stored in Kogwistar as an event-sourced hypergraph.

There is one underlying graph substrate. Different application graph spaces are
logical partitions over that substrate, not separate ontologies.

---

### Engine Graph Kind

The broad graph family configured on a `GraphKnowledgeEngine`.

Examples:

- `knowledge`
- `conversation`
- `workflow`
- `wisdom`

Engine graph kind is a storage/runtime family. It is intentionally coarse.

Do not use engine graph kind to distinguish `source`, `base_kg`, or
`curated_kg`. Those are application graph spaces or semantic layers inside the
knowledge family.

---

### Graph Space

A logical application partition of graph objects with a shared purpose.

Examples:

- SOURCE
- BASE_KG
- CURATED_KG
- CONVERSATION
- WORKFLOW
- REVIEW
- WISDOM
- POLICY
- PROJECTION

Graph spaces are normally represented by namespace plus metadata. They are not
node types, edge types, or Kogwistar engine graph kinds.

---

### Namespace

An operational storage, replay, and routing partition.

Examples:

- `ws:{workspace_id}:g:source`
- `ws:{workspace_id}:g:base_kg`
- `ws:{workspace_id}:g:curated_kg`
- `ws:{workspace_id}:g:conversation:lane:foreground`
- `ws:{workspace_id}:g:conversation:lane:background`

Namespace should be a deterministic projection of workspace scope, graph space,
and optional lane. It is useful for storage isolation and replay, but should not
be the only place semantics are recorded.

---

### Metadata

Inspectable semantic attributes stored on documents, nodes, edges, events, or
artifacts.

Examples:

- `workspace_id`
- `graph_space`
- `knowledge_layer`
- `artifact_kind`
- `conversation_lane`
- `visibility`
- `verification_status`

Metadata is intentionally partly redundant with namespace. Namespace routes data;
metadata explains data. Invariants should ensure they agree.

---

### Node

A unit of information in the graph.

Examples:

- message
- entity
- concept
- source document
- parsed section
- fact candidate
- review item
- workflow run
- wisdom insight

Node type should describe the shape or semantic role of the node, not the graph
space where it is stored.

---

### Edge

A relationship between nodes.

Examples:

- `mentions`
- `derived_from`
- `supports`
- `contradicts`
- `proposes_link`
- `has_section`
- `grounded_in`

Edge types should express semantic relationship meaning, not graph space,
workflow stage, or storage namespace.

---

### Artifact Kind

The lifecycle or product role of an artifact.

Examples:

- `promotion_candidate`
- `promotion_evidence_pack`
- `promoted_knowledge`
- `derived_knowledge`
- `execution_wisdom`

Artifact kind is not graph space. A `promotion_candidate` typically belongs in
REVIEW, while `promoted_knowledge` belongs in CURATED_KG.

---

## 2. Scope, Namespace, and Policy

### Workspace

An LLM-wiki application scope, roughly a project or vault boundary.

Workspace is not a workflow and not a graph space. It groups graph spaces,
queues, projections, and maintenance activity for one app-level context.

Current LLM-wiki code uses `workspace_id` for namespace construction, metadata
filtering, daemon routing, projection jobs, and maintenance jobs.

---

### Security Scope

The access-control boundary used by policy and ACL evaluation.

Security scope may align with tenant, workspace, project, document family, or a
future sharing model. It should remain distinct from storage namespace and
execution namespace.

---

### Principal / Subject

The actor whose rights are evaluated.

Examples:

- user
- team member
- agent
- service account
- workflow actor

Do not overload graph namespace with principal ownership unless a policy design
explicitly requires it.

---

### Owner

An ambiguous term that should be avoided unless scoped.

Possible meanings include uploader, workspace owner, ACL principal, service
account, workflow actor, or artifact custodian. Prefer more precise terms such
as `principal`, `subject`, `workspace_id`, `created_by`, or `security_scope`.

---

## 3. Knowledge-Domain Graph Spaces

### SOURCE

The authoritative source/evidence layer.

Contains:

- raw document nodes
- parsed page nodes
- section nodes
- paragraph/span nodes
- document structure edges
- deterministic parser provenance
- source maps and grounding pointers

Characteristics:

- written after successful parsing
- queryable directly
- not equivalent to accepted knowledge
- should not be hidden only inside conversation memory

---

### BASE_KG

The automatically extracted knowledge layer.

Contains:

- machine-extracted entities
- terms
- candidate facts
- candidate relations
- source-referenced graph projections

Characteristics:

- queryable immediately
- marked unverified or machine-extracted
- linked back to SOURCE via explicit reference artifacts
- not equivalent to curated/promoted knowledge

Example metadata:

- `knowledge_layer: "base_kg"`
- `extraction_status: "machine_extracted"`
- `verification_status: "unverified"`

---

### CURATED_KG

The accepted/promoted knowledge layer.

Contains:

- reviewed facts
- accepted entities
- validated relationships
- stable domain knowledge

Characteristics:

- authoritative for curated knowledge
- policy/review/confidence gated
- linked back to REVIEW evidence and SOURCE/BASE_KG provenance
- projected to user-facing knowledge surfaces when eligible

Use `curated_kg` instead of ambiguous `kg` in new semantics. Existing `kg`
strings may remain as legacy aliases during migration.

---

## 4. Other Graph Spaces

### CONVERSATION

Working memory and interaction state.

Contains:

- user messages
- assistant responses
- lane messages
- temporary interaction artifacts

Characteristics:

- high churn
- not authoritative source storage
- foreground/background is a lane inside conversation, not a graph space for
  source documents

---

### WORKFLOW

Execution and process state.

Contains:

- workflow runs
- job status
- execution traces
- maintenance requests
- process checkpoints

Workflow is not the same thing as workspace.

---

### REVIEW

Evaluation and promotion-review state.

Contains:

- promotion candidates
- evidence packs
- approvals
- rejection reasons
- audit decisions

Review artifacts should not be stored only as background conversation artifacts.

---

### WISDOM

Reusable, distilled, execution-derived knowledge.

Contains:

- patterns
- strategies
- lessons learned
- reusable heuristics

Characteristics:

- derived from source, base, curated, conversation, or workflow artifacts
- not raw source truth
- should not become mutable runtime state

---

### POLICY

Governance and access-control semantics.

Contains:

- ACL state
- capabilities
- quotas
- approvals
- governance rules
- visibility policy artifacts

Policy graph semantics should remain distinct from storage namespace and
execution namespace.

---

### PROJECTION

Derived external or materialized views of graph state.

Examples:

- Obsidian markdown files
- named projection manifests
- search/materialized index views

Projection is rebuildable and not authoritative.

---

## 5. Identity & Determinism

### Stable Identity

A deterministic identifier, such as uuid5-based identity, used to ensure:

- idempotency
- replay stability
- deduplication

Identity is machine-oriented, not necessarily human-readable.

---

### Deterministic Artifact

An artifact whose identity is derived from:

- inputs
- pipeline version
- context

---

### Fragment

A structured portion of a document, such as a section, paragraph, table cell, or
span.

Fragment is a semantic role, not an ID type.

---

### Derived Artifact

An artifact produced from other artifacts, such as a summary, link, cluster, or
wisdom item.

Derived status is expressed through provenance and relations, not ID prefixes.

---

## 6. Grounding & Provenance

### Span

The smallest addressable unit of evidence.

Examples:

- text range
- bounding box
- table cell

---

### Grounding

A set of one or more spans that support an artifact.

Grounding answers: "Where exactly does this come from?"

---

### Provenance

The full lineage of an artifact:

- inputs
- transformations
- workflow steps
- evidence packs
- review decisions

Provenance answers: "How was this created?"

---

## 7. Lifecycle Concepts

### Source Ingestion

The act of writing parsed source material into SOURCE.

Source ingestion is not promotion.

---

### Base Extraction

The act of projecting machine-generated references from SOURCE into BASE_KG.

Base extraction is not promotion. It may be driven by page-index parsing,
iterative layerwise parsing, user-provided parsed input, deterministic
extractors, or configurable LLM extractors.

---

### Promotion

The act of creating a new authoritative curated artifact in CURATED_KG, or a
new accepted distilled artifact in WISDOM, based on evaluated inputs.

Promotion does not move or mutate existing SOURCE or BASE_KG nodes. It creates
new artifacts with provenance links.

---

### Promoted Artifact

A node or edge that exists in CURATED_KG or WISDOM after promotion.

---

### Candidate

An artifact not yet promoted.

Typically lives in:

- BASE_KG
- REVIEW
- WORKFLOW
- CONVERSATION

---

### Superseded

An artifact that has been replaced by a newer version.

---

### Tombstoned

An artifact that is logically deleted but still exists in event history.

---

## 8. Relation Semantics

### Relation Type

The semantic meaning of an edge.

Examples:

- `mentions`
- `derived_from`
- `supports`
- `contradicts`
- `has_section`
- `grounded_in`

Should not encode:

- graph space
- workflow stage
- agent type
- storage namespace

---

### Cross-Space Relation

A relation where endpoints belong to different graph spaces.

Cross-space status is inferred from endpoint metadata and namespace, not encoded
in the relation type.

---

## 9. Pin / Ref / Projection

### Pin

A mechanism to stabilize or anchor a reference to an artifact across contexts.

---

### Ref

An explicit reference artifact that points from one graph object to another.

Used for:

- linking
- reuse
- contextualization

A ref is not any node or edge that merely happens to have similar fields. The
artifact must explicitly mark itself as a reference.

---

### Projection

A derived external representation of graph state.

Projection is:

- not authoritative
- rebuildable from the graph
- allowed to have its own namespace or manifest

---

## 10. Maintenance vs Reasoning vs Wisdom

### Maintenance

System processes that:

- evaluate candidates
- resolve conflicts
- improve structure
- schedule review or derivation

Maintenance is a process, not a graph space by itself. Maintenance artifacts may
live in WORKFLOW, REVIEW, or CONVERSATION depending on their role.

---

### Reasoning

Temporary or intermediate thinking.

Usually lives in:

- CONVERSATION
- WORKFLOW
- REVIEW

---

### Wisdom

Generalized, reusable knowledge derived from execution.

Wisdom answers: "What tends to work and why?"

---

## 11. Key Invariants

- Graph substrate is authoritative.
- Events are append-only.
- No in-place mutation of semantic truth.
- Identity is deterministic.
- Namespace routes data; metadata explains data.
- Namespace and metadata must agree.
- Workspace scope is not graph space.
- Engine graph kind is not graph space.
- SOURCE is queryable before promotion.
- BASE_KG is distinguishable from CURATED_KG.
- CURATED_KG contains accepted/promoted knowledge.
- Conversation is not the source-document store.
- Promotion creates new curated or wisdom artifacts.
- Grounding must be preserved.
- Projection is derived, not primary.
- Semantics belong in metadata and relations, not only naming conventions.

---

## 12. Common Confusions

| Confusion | Correct understanding |
| --- | --- |
| `source` = engine graph type | No, SOURCE is an application graph space / semantic layer. |
| `base_kg` = node type | No, BASE_KG is a knowledge-domain graph space. |
| `curated_kg` = all KG data | No, CURATED_KG is accepted/promoted knowledge only. |
| `kg` = precise semantic term | No, use `curated_kg`, `base_kg`, or knowledge engine/family. |
| workspace = workflow | No, workspace is app/project scope; workflow is execution/process state. |
| namespace = complete semantics | No, namespace routes; metadata records semantics. |
| graph space = relation type | No, infer cross-space from endpoint metadata/namespace. |
| fragment = ID type | No, fragment is a semantic role. |
| derived = ID prefix | No, use provenance and relations. |
| promotion = source ingestion | No, promotion creates accepted curated/wisdom artifacts. |
| maintenance = graph type | No, maintenance is a process. |
| wisdom = single node | No, WISDOM is a graph space. |
| projection = source of truth | No, projection is rebuildable derived output. |

---

END
