# ADR: Native Multimodal Embedding Retrieval

- Status: Accepted, with provider-free dense Qwen3-VL and legacy ColQwen slices implemented
- Scope: LLM-Wiki multimodal source capture, embedding projections, retrieval,
  provenance, agent APIs, archive, and workbench presentation
- Source cases: images, PDFs, plain text, and webpages containing images
- Related: `adr_interactive_graph_explorer.md`,
  `adr_conversation_two_stage_materialization.md`, `adr_archive_recovery.md`

## Production Inference Boundary

Production Qwen3-VL inference runs in the isolated FastAPI representation
service (`Dockerfile.representation-service`). The standalone service is built
from the `llm-wiki-representation-service` distribution and its stdlib-only
`llm-wiki-representation-contract` dependency. It does not import the
LLM-Wiki application, Kogwistar, parser, sink, Chroma, PostgreSQL, or MCP.
The normal LLM-Wiki image stays Torch-free and uses
`LLM_WIKI_REPRESENTATION_SERVICE_URL` through the typed remote encoder. One
service process owns one dense, profile-pinned
`Qwen/Qwen3-VL-Embedding-2B` model; CPU and CUDA images are separate.

`LLM_WIKI_REPRESENTATION_MODEL_REVISION` is mandatory and must be an immutable
Hugging Face commit, tag, or other verified revision. It is part of the profile
fingerprint; a floating model reference is rejected at service startup. The
HTTP contract remains version `v1`, while the two distributions can be
installed and tested independently of the application distribution.

The service accepts only bounded text and resolved asset bytes with a MIME type
and SHA-256. It never fetches source URLs or filesystem paths. LLM-Wiki keeps
source identity, source maps, grounding, Stage 1 capture, retries, and Stage 2
promotion. A service outage leaves Stage 1 pending and reports multimodal
retrieval as degraded; it does not fall back to a different vector space.

The existing in-process Qwen and ColQwen adapters remain for provider-free
tests, migration compatibility, and developer benchmarking. They are not the
production container path. A remote profile must match the stored profile
exactly, including model revision, dimension, preprocessing, instruction,
normalization, metric, and representation. Equal dimensions alone are not
compatible.

## Context

LLM-Wiki currently treats semantic retrieval as text embedding. One embedding
function is configured per graph engine, persisted vectors are guarded by an
`EmbeddingProfile`, and source ingestion begins with `raw_text`. This works for
plain text but cannot perform native image-to-text, text-to-image, or
image-to-image retrieval.

The current substrate also assumes one vector per stored record and an
embedding callable whose input is `list[str]`. A native multimodal model has a
different contract:

- text and images enter through different preprocessors or encoder towers;
- both outputs are comparable only when the model explicitly defines one
  shared semantic space;
- image size, crop/tiling policy, text prompt/template, vector normalization,
  and model revision affect compatibility as much as dimension does;
- some document models emit multiple vectors per page and require late
  interaction rather than one nearest-neighbor score;
- a retrieved vector identifies a candidate source unit, not a factual claim.

The product needs one design that handles these source shapes correctly:

- a standalone image;
- a PDF containing selectable text, scanned pages, figures, and tables;
- a plain-text or Markdown document;
- a webpage whose meaning is split across DOM text, captions, and images.

This ADR is self-contained. It does not require textual OCR/caption derivatives
to be the primary retrieval representation. Such derivatives may still be
created for accessibility, grounding inspection, text-only answer models, or
fallback search, but native media vectors are a first-class retrieval path.

## Decision

Add an app-owned **multimodal retrieval plane** beside the existing knowledge
graph and text retrieval plane.

```text
source bundle
  -> immutable assets and source occurrences
  -> typed retrieval views
  -> multimodal embedding jobs
  -> isolated profile-bound vector projection
  -> grouped cross-modal candidates
  -> ACL filter + graph expansion + reranking
  -> grounded answer, lens, or proposal
```

The authoritative source graph remains event-sourced and provenance-first.
The multimodal vector store is a rebuildable named projection. It never becomes
graph truth, source truth, or an alternate mutation path.

### Implemented reference slice

The application now contains a provider-free reference implementation in
`src/kogwistar_llm_wiki/multimodal_projection.py`:

- `MultimodalSourceUnit` is a revision-bound Stage-1 view containing locators
  and an external `content_ref`, not copied binary source bytes;
- `MultimodalEmbeddingProfile` binds provider, model, representation,
  dimension, metric, preprocessing, sequence, and patch limits;
- `InMemoryMultimodalProjectionStore` and
  `SQLiteMultimodalProjectionStore` enforce profile identity and preserve
  Stage-1 progress across restart;
- `ChromaMultimodalProjectionStore` adds an optional persistent adapter that
  stores one token/patch row per view in an isolated Chroma collection and
  keeps profile/Stage-1 state in a colocated SQLite sidecar. Its reference
  search performs exact grouped MaxSim only within a configured vector-count
  bound and fails closed above that bound instead of attempting an unbounded
  read; production-scale ANN candidate retrieval remains a follow-up;
- `FakeMultimodalEncoder` and `score_embedding_sets()` exercise deterministic
  pooled and ColBERT/ColQwen-style late-interaction behavior without a model;
- `multimodal_sources.py` provides a dependency-light source-bundle adapter:
  text is split into exact half-open spans, HTML yields visible page text plus
  separate image and table occurrences, and normalized PDF/parser manifests
  yield separate page, image, table, and chart units;
- `MappingAssetResolver` and `LocalFileAssetResolver` demonstrate the explicit
  external-asset resolution boundary without copying source bytes into the
  graph or projection store;
- `multimodal_grounding.py` provides an application-owned higher-order
  grounding contract with typed node/edge references, pinned revisions and
  event watermarks, direct source terminals, content hashes, namespace checks,
  and bounded cycle-safe closure validation. It reuses Kogwistar
  `LogicalRef` and does not alter core `Grounding` or create a direct graph
  mutation path;
- `ColQwenNativeEncoder` is an optional native Transformers adapter that keeps
  heavy dependencies out of normal imports and loads 4-bit CUDA weights when
  requested. Mixed source batches route text-bearing views through the text
  processor and visual views through the image processor while preserving
  source order; unresolved webpage-only references fail instead of being
  embedded as fabricated content;
- `Qwen3VLDenseEncoder` is the default native multimodal adapter for
  `Qwen/Qwen3-VL-Embedding-2B`. It emits one normalized dense vector per
  source unit for text, images, and mixed inputs. Its MRL dimension is
  configurable from 64 through 2048, with 1024 recommended. Its `dense`
  profile is incompatible with text-only Qwen3-Embedding and ColQwen
  late-interaction profiles even when dimensions match;
- `IngestPipeline` exposes opt-in capture, Stage-2 promotion, and multimodal
  query methods without changing the default text ingestion flow.

The source adapter does not fetch URLs or parse/OCR PDF bytes, and the
late-interaction adapter does not yet claim pgvector persistence. URL retrieval,
content-addressed asset storage, and PDF/OCR remain service/parser work; the
adapter consumes their validated references and normalized manifests. The
higher-order validator is ready for app-level proposal/persistence adapters,
but the existing core `Grounding` model and all existing text persistence
paths remain unchanged until that additive contract is adopted at their
boundary. The existing text ingestion path remains unchanged unless callers
opt into this multimodal plane.

### Qwen3-VL profile and legacy ColQwen comparison

The native default is [`Qwen/Qwen3-VL-Embedding-2B`](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B).
Use 1024 dimensions for the default production balance, 1536 for a larger
pgvector profile, or 2048 for Chroma/full-output experiments. Standard
pgvector HNSW does not support ordinary `vector(2048)` indexes, so a 2048
profile must use Chroma, exact/non-indexed storage, or a future `halfvec`
projection. Changing model, representation, or dimension requires a new
profile and re-embedding; vectors are never silently reused.

The old ColQwen adapter remains available only as an explicit legacy
late-interaction comparison route. It is not the default and cannot share a
dense Qwen3-VL projection.

### Legacy ColQwen developer profile

The recommended first real checkpoint is
[`vidore/colqwen2-v1.0-hf`](https://huggingface.co/vidore/colqwen2-v1.0-hf), a
2B-parameter ColQwen model with a native Transformers retrieval class.
The [Transformers ColQwen2 guide](https://huggingface.co/docs/transformers/model_doc/colqwen2)
documents NF4 4-bit loading with `BitsAndBytesConfig`; this is load-time
quantization, not a modified model checkpoint. Download it with:

```text
python scripts/pull_colqwen_model.py --local-dir data/models/colqwen2-v1.0-hf
```

The repository's verified development checkpoint is revision
`ddc07d2317c80f75fc742b7362ee9ad1912908f9`; pin `--revision` for reproducible
deployments and override it only as an intentional model upgrade.

The `multimodal` extra pins the tested portable CPU-capable Torch generation.
CUDA builds cannot be represented by one universal wheel because the correct
index varies by OS, Python ABI, and the deliberate CUDA target. The repository
therefore checks in `requirements/multimodal/torch-cpu.txt`,
`torch-cu126.txt`, and `torch-cu128.txt`. The CPU profile installs the
CPU-only Torch wheel first, followed by the dependency-only
`.[multimodal-cpu]` extra. For CUDA, install the selected requirements profile
first and then `.[multimodal-cuda]`; this keeps the official CUDA index choice
explicit. If the selected CUDA build has no usable device, it fails with a
driver/GPU-passthrough diagnostic. If the CPU profile is chosen,
`device="cuda"` must not be configured. Quantization is a load-time choice and
does not change the pinned checkpoint files. Every native profile also installs
`accelerate`; Transformers requires it for the CUDA loader's
`device_map="auto"` placement. Operators should not need to install it as an
undocumented follow-up step. The VLM linear layers use NF4, but the final
`embedding_proj_layer` remains floating point: ColQwen derives the projection
input dtype from that head, so quantizing it would expose packed `uint8`
storage and break embedding normalization.

The base application Dockerfile does not install Torch. Production inference
uses `Dockerfile.representation-service`, whose CPU and CUDA variants install
the selected Torch profile and expose the FastAPI contract. The in-process
installation steps in this section are retained only for local development,
migration compatibility, and provider-free adapter work; they are not a
supported production deployment boundary.

### Remote and alternate runtimes

The provider name must describe **retrieval semantics**, not merely which
process hosts a vision model. A vision chat service can caption an image, but
that is a derived-text workflow and is not a native image/text embedding space.
It must remain distinct in provenance, ranking explanation, and profile
identity.

| Route | Current status | Safe use and admission rule |
| --- | --- | --- |
| `qwen3-vl-representation-service` | Implemented as the production FastAPI sidecar | Native dense text/image projection. The profile pins model revision, preprocessing, instruction, normalization, metric, and dimension. |
| `colqwen-local` | Developer/migration compatibility only | Native late-interaction text/image projection. It is not interchangeable with the dense service profile. |
| `multimodal-ollama-extract` | Follow-up extraction adapter | Ollama's documented `/api/embed` input is text or an array of texts, so it is not admitted as native cross-modal embedding. A vision model may produce OCR/captions as derived evidence, then the normal text embedding plane indexes that text. |
| `multimodal-vllm` | Follow-up remote projection adapter | vLLM supports both multimodal generation and pooling/embedding workloads, but an adapter must capability-probe one deployed model and prove that its embedding route accepts the required media inputs with stable output shape. Do not infer this from chat support. |
| `multimodal-llamacpp` | Experimental follow-up remote projection adapter | llama.cpp documents multimodal support for its non-OpenAI `/embedding` route, but that path is experimental and has a provider-specific media payload. It requires a model/mmproj hash, server-build fingerprint, fixture verification, and cannot use the generic OpenAI embeddings route as an equivalence claim. |

Each future remote adapter must implement the same typed source-unit and
embedding-set contracts, declare a complete profile (provider endpoint,
backend/server build, model revision or content hash, model-projector hash,
preprocessing, representation, dimension, metric, and capability shape), and
pass these admission tests before Stage 2 writes:

1. text, image, and mixed batch requests preserve input ordering;
2. image changes alter the returned representation where the model promises
   native image embedding;
3. text/image output dimensions, normalization, and scoring convention are
   stable and declared;
4. restart/reopen produces the same profile fingerprint; and
5. cross-provider retrieval equivalence is measured on a frozen fixture set,
   rather than assumed because two deployments claim the same model name.

This allows a hosted implementation of a model to be added later without
changing grounding, source maps, Stage 1, Stage 2, or the graph. It also keeps
a caption/OCR-maintainer route useful without falsely presenting it as native
cross-modal retrieval. Relevant upstream contracts are the
[Ollama embeddings API](https://docs.ollama.com/api/embed),
[vLLM model/pooling support](https://docs.vllm.ai/en/latest/models/supported_models/),
and [llama.cpp multimodal server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

For this local multi-repository checkout, install `kogwistar`,
`kg-doc-parser`, and `kogwistar-obsidian-sink` from their sibling directories
before installing the multimodal profile. Pip does not consume the repository's
`tool.uv.sources` table, so a direct pip install in a fresh checkout must first
install the local sibling distributions. Then use `.[multimodal-cpu]` for CPU,
or install a `torch-cu126.txt`/`torch-cu128.txt` profile followed by
`.[multimodal-cuda]` for CUDA. This avoids trying to find the published
distribution `graph-knowledge-doc-parser` on PyPI. Install
`torch-cpu.txt` followed by `.[multimodal-cpu]` for CPU. Use
`PYTHON_BIN=.venv/bin/python bash scripts/bootstrap-dev.sh` on Bash, or the
explicit sibling install commands in the cookbook on PowerShell.

Use `load_in_4bit=True`, `device="cuda"`, `max_image_patches=768`, and a
default document `batch_size=1` as the safe starting point for an 8 GB card.
Increase document batching only after measuring peak VRAM and checking that
the installed processor produces batch-size-stable embeddings. Query batches
of 2--4 are a reasonable first experiment. The profile records the patch and
sequence limits so preprocessing changes cannot silently reuse old vectors.

`vidore/colqwen2.5-v0.2` is a stronger but larger 3B-family alternative. Its
model card reports dynamic resolution with up to 768 patches and a BF16 base
that is too close to an 8 GB card's limit before runtime memory is included.
It is therefore not the default local profile. If 4-bit loading is unavailable
on the host, use the much smaller `vidore/colSmol-500M` late-interaction model
or a remote inference worker; do not pretend that a pooled text embedder is a
drop-in replacement for ColQwen.

Use a shared-space multimodal embedding model for the first implementation.
Its text encoder and image encoder must produce vectors documented as directly
comparable under one metric. Text queries can therefore retrieve images and
page renders, while image queries can retrieve text passages and other images.

Keep the existing knowledge embedding space separate by default. Knowledge
nodes are semantic assertions and summaries; multimodal records are evidence
views of source material. They have different update, retention, ACL, and
ranking behavior even when the same model could embed both.

## Compatibility With Existing Graph Primitives

The base graph model should be reused rather than replaced:

- `Node` remains a knowledge or source entity.
- `Edge` remains an ordinary or multi-endpoint relationship, including the
  existing hyperedge-shaped endpoint lists.
- `Grounding` remains the evidence owned by a node or edge.
- `Span` remains the exact, half-open character locator for text.
- source maps remain the authoritative resolver from stable source-unit IDs to
  revision-bound content and locators.

The smallest sound model evolution is to add typed non-text references beside
the existing spans:

```text
Grounding
  spans: list[Span]                         # unchanged text evidence
  source_refs: list[SourceEvidenceRef]      # new media evidence
  evidence_pack_refs: list[EvidencePackRef] # grounded upstream entities

SourceEvidenceRef =
  PageRegionRef | TimeRangeRef | FrameRegionRef | WholeAssetRef

EvidencePackRef
  artifact_ref: LogicalRef                  # current pack is an artifact-kind Node
  expected_hash: str
  source_watermark: int | map[namespace, int]
```

`Grounding` validates that at least one of `spans`, `source_refs`, or
`evidence_pack_refs` is present. An evidence-pack reference is valid only when
its resolved upstream closure terminates in direct source evidence; it cannot
make an ungrounded assertion authoritative merely by referring to another
ungrounded assertion.

Existing payloads, `iter_span()`, text extraction, flattened span indexes, and
event replay remain valid. New code uses `iter_evidence()` when it needs all
modalities. A grounding may contain both a PDF text span and a figure region,
which naturally represents evidence that needs both.

Each `SourceEvidenceRef` carries source document ID, immutable revision ID,
source-unit ID, locator kind, locator fields, verification, and locator digest.
Validation resolves the source-unit ID through the revision's source map and
checks that the copied locator and digest match. This preserves the current
source-map principle instead of putting storage URLs into graph entities.

The current model-level `Grounding.validate_from_source()`/
`Grounding.validate_span()` hooks do not themselves resolve authoritative
documents or source maps. The multimodal work must not extend that placeholder
behavior. Add one persistence-boundary `GroundingValidator` that receives a
revision-aware `SourceMapResolver` and rejects, before the node/edge event:

- a text span whose document, offsets, or excerpt do not resolve;
- a media reference whose source unit, revision, locator, or digest differs;
- an evidence pack whose hash, typed entity references, revisions, or source
  watermark do not resolve;
- a cross-workspace or unauthorized source reference;
- a self-reference, future reference, or cyclic derived-grounding chain;
- an inferred derivative presented as direct source evidence.

Pydantic validators enforce local shape; the persistence validator enforces
referential and source-content truth. The same validator is used by ingestion,
maintenance, proposal confirmation, archive restore, and seed import so modal
grounding cannot bypass the text path's integrity rules.

Do not turn `Span` into one large model with optional character, bounding-box,
time, frame, and asset fields. That appears smaller initially, but it weakens
typing, makes invalid combinations easy, and forces every existing text-span
consumer to understand every modality. If a future breaking model version is
acceptable, `Span` can become the `TextSpan` member of a general
`EvidenceLocator` union; it should not be mutated into an ambiguous bag of
fields.

Do not use graph or hypergraph edges as the sole grounding representation.
That would make a node's validity depend on a later graph traversal and allow a
node event to exist without its evidence event. Grounding should remain embedded
and atomically validated with the node or edge. Graph relationships may project
`grounded_by`, `contains`, `depicts`, `caption_for`, and similar links for
navigation, ACL walks, and multi-source reasoning, but those links supplement
rather than replace `Grounding`.

This keeps `Span` important rather than legacy: text remains a first-class
modality, and exact text offsets are more precise than a generic hypergraph
pointer.

## Higher-Order Grounding Through Existing Entities

A higher-order node or edge may be justified by several existing nodes,
ordinary edges, and hyperedges. This is already close to the current model:

- `LogicalRef` distinguishes node, edge, and artifact targets and carries the
  target namespace;
- `EvidencePackDigest` keeps `node_ids` and `edge_ids` separate and hashes the
  canonical pack content;
- `Edge.source_ids` and `Edge.target_ids` support multiple node endpoints;
- `Edge.source_edge_ids` and `Edge.target_edge_ids` support relationships whose
  endpoints include other edges.

There is no separate persisted hyperedge kind in this contract. A hyperedge is
an `Edge` with multiple participants, possibly including edge endpoints.
Consequently, a hyperedge used as evidence appears in
`EvidencePackDigest.edge_ids`. Readers determine its shape by resolving the
edge; they must not guess the entity kind from an untyped mixed ID list.

### Keep topology and evidence distinct

Endpoint references answer **what the relation connects**. Grounding answers
**what justifies the assertion or relation**. They often overlap, but neither
implies the other.

For example, a high-order `tradeoff_between` edge might connect two technique
nodes through `source_ids` and `target_ids`. Its grounding pack could cite a
benchmark-result node, a binary `measured_on` edge, and a benchmark-context
hyperedge joining model, dataset, hardware, and metric. The endpoint fields do
not prove the tradeoff, and the evidence pack does not change the tradeoff's
topology.

The same rule applies when the high-order entity is a node. A summary node such
as "method A improves quality under condition C" can cite several grounded
claim nodes and relation edges without duplicating every source span. Its
grounding still remains embedded in the new node through an immutable
`EvidencePackRef`, so node creation and evidence validation form one
persistence unit.

### Reuse the evidence-pack contract

Do not add an independent list of loose graph IDs to `Grounding`. Reuse the
existing durable evidence-pack artifact and `EvidencePackDigest`:

```text
Higher-order Node or Edge
  mentions[].evidence_pack_refs[]
    -> LogicalRef(namespace, "node", evidence_pack_node_id)
    -> expected EvidencePackDigest hash

Evidence-pack artifact node
  artifact_kind: evidence_pack
  node_ids: [claim_node, measurement_node, ...]  # compatibility projection
  edge_ids: [ordinary_edge, hyperedge, ...]      # compatibility projection
  node_refs: [{logical_ref, revision, event_seq, role}, ...]
  edge_refs: [{logical_ref, revision, event_seq, role}, ...]
  source_watermarks: {namespace: seq}
  composition: all_of | any_of
  source_closure_hash: digest of terminal source evidence
```

The current llm-wiki promotion path persists an evidence pack as a node whose
metadata identifies its artifact kind. The reference therefore uses
`target_kind="node"` today and validates the expected artifact kind after
resolution. A future first-class artifact collection may use
`target_kind="artifact"`, but this ADR does not pretend that collection already
backs the current evidence-pack path.

The typed reference, watermark, composition, and closure fields fit the
existing `EvidencePackDigest` extensibility contract, which preserves extra
canonical fields in its hash. The existing `node_ids` and `edge_ids` remain a
compatible local-ID projection; validators and cross-namespace readers use the
typed references. Before adopting the extension, its exact canonical ordering
and portable representation must receive Python/Rust parity and archive replay
tests. `LogicalRef` remains useful for locating live entities and the durable
pack, but it is not sufficient by itself as immutable provenance because it
does not pin an entity revision or event watermark.

`composition` is explicit rather than inferred from list shape:

- `all_of` means every listed item participates in one combined justification,
  such as a figure, its legend, and its explanatory paragraph;
- `any_of` means each listed item is an independently sufficient corroborating
  path.

Regardless of composition, every supplied reference must resolve and pass ACL,
revision, and integrity validation. `any_of` is not permission to retain stale,
fabricated, or inaccessible references. More complex nested Boolean proof
expressions are deferred until a measured use case requires them.

### Validation and cycle safety

`GroundingValidator` resolves each pack at the proposal/persistence
watermark and enforces:

1. The evidence-pack node exists in the permitted workspace and namespace, has
   the expected artifact kind, and its recomputed hash equals `expected_hash`.
2. Every node and edge exists at its pinned revision. Edge IDs may resolve to
   binary, multi-node, edge-to-edge, or other hyperedge-shaped edges.
3. Every referenced semantic entity is itself directly grounded or has a
   previously committed, valid evidence-pack path to direct source evidence.
4. The dependency graph is acyclic: no self-reference, no path back to the new
   entity, and no reference to a future event or unresolved draft.
5. At least one terminal path reaches a verified `Span` or
   `SourceEvidenceRef` for factual knowledge. Pure workflow objects may instead
   terminate in explicit execution lineage, as required by the existing
   grounding and lineage invariant.
6. Required evidence is visible under the target ACL and scope policy. A
   higher-order claim cannot launder inaccessible evidence into a broader
   workspace.

Validation uses a bounded, memoized traversal and stores the verified terminal
source-closure hash on the pack. Reads can therefore show immediate supporting
entities and a compact source summary without recursively walking the entire
graph. The authoritative validator still recomputes the bounded closure when a
proposal is confirmed, imported, or restored.

Updates create new entity and evidence-pack revisions rather than mutating an
old proof in place. Tombstoning an upstream item does not cascade-delete the
higher-order entity or its historical evidence. It marks the current
derivation stale or `review_required`; maintenance may then produce a new pack
and revision. Historical queries pinned before the tombstone remain
reproducible.

### Retrieval and display

A semantic lens may return both the higher-order entity and a bounded evidence
expansion. The response distinguishes:

- topology participants;
- immediate evidence nodes and edges;
- resolved terminal source regions;
- omitted evidence counts and reasons;
- evidence status such as current, stale, redacted, or review required.

The UI can summarize this as "supported by 2 claims, 2 relations, and 4 source
regions" and expand the pack on demand. A hyperedge remains a first-class
relation card or hub; it must not be flattened into a clique when shown as
evidence.

The resulting dependency shape is explicit and acyclic:

```text
high-order node or edge
  |-- topology --> participant node(s) and/or edge(s)
  `-- grounding --> evidence-pack artifact at hash H
                       |-- node ref @ revision R1
                       |-- ordinary-edge ref @ revision R2
                       `-- hyperedge ref @ revision R3
                              `-- validated grounding closure
                                   --> Span / SourceEvidenceRef
```

Only the grounding branch establishes evidence. The topology branch remains a
navigation and relation structure even when it names some of the same entities.

## Logical Architecture

### 1. Source bundle and asset store

A source revision is a bundle rather than one text string. It contains:

- one root source identity and immutable revision;
- content-addressed assets identified by SHA-256;
- logical occurrences of those assets in a PDF page or webpage DOM;
- source metadata, ACL, capture information, and provenance policy;
- exact locators connecting each retrieval view to its source occurrence.

Media bytes live in an app-owned content store, not graph events or vector
metadata. Physical bytes may be deduplicated by hash, while logical occurrences
remain distinct because the same image can have different meaning on two pages
or webpages.

The store is accessed through an `AssetStore`/`AssetResolver` capability rather
than direct paths:

```text
put_stream(...) -> immutable AssetDescriptor
open(asset_id, principal, byte_range=None) -> authorized stream
resolve_view(source_unit_id, revision_id, principal) -> bytes or text
verify(asset_id, expected_sha256) -> verification result
export/import inventory -> archive integration
```

Local files, S3-compatible storage, Azure Blob Storage, and other backends can
implement the same contract. The graph persists the asset ID and checksum, not
the backend URI. Embedding and answering workers resolve source views just in
time, verify the checksum, and release streams promptly. This allows source
storage to be completely external to Chroma or PostgreSQL while keeping
retrieval, replay, and provider calls deterministic.

### 2. Retrieval view

A retrieval view is the smallest unit sent to an embedding model. Its contract
contains:

- stable view ID;
- workspace, source document, revision, asset, and occurrence IDs;
- input modality and view kind;
- exact source locator;
- bytes or text obtained through an authorized internal resolver;
- parent/group ID used to aggregate several views into one result;
- embedding profile and projection version;
- optional nearby context IDs, never silently concatenated to image bytes.

Initial view kinds are:

- `text_passage`;
- `whole_image`;
- `image_region`;
- `pdf_page_image`;
- `pdf_figure`;
- `web_image_occurrence`.

Embedding records contain the view pointer and operational metadata, not the
full source payload.

The source map becomes a typed superset of the current chunk map:

```text
SourceMapEntry
  source_unit_id
  source_document_id
  source_revision_id
  locator: TextSpanRef | PageRegionRef | TimeRangeRef | FrameRegionRef | WholeAssetRef
  asset_id and content_sha256
  parser_text_ref, if any
  parent_unit_id and ordering metadata
```

Existing text chunk maps adapt directly: the chunk ID becomes
`source_unit_id`, its half-open offsets become `TextSpanRef`, and current
`Span.chunk_id`/`source_cluster_id` resolution remains available. Parser OCR
`GroundedSourceRecord` maps page, region, and bounding-box data into the same
entry. This avoids separate text and media source-map systems.

Source maps are immutable per source revision. Re-ingestion creates a new map;
it never changes what an old citation resolves to.

### 3. Multimodal embedding adapter

Introduce a typed app-facing protocol rather than widening the existing text
callable with untyped values:

```text
embed_texts(profile, text inputs) -> list[EmbeddingSet]
embed_images(profile, image inputs) -> list[EmbeddingSet]

EmbeddingSet
  vectors: matrix[float]
  representation: single_vector | late_interaction
  vector_roles: optional patch/token coordinates
  scoring_operator: cosine | dot | l2 | maxsim
```

Both operations must return the same vector dimension and normalization
convention for a shared-space profile. The number of vectors per input may
differ: a pooled Sentence-Transformers/CLIP-style adapter normally returns one
vector, while a ColQwen/ColPali-style late-interaction adapter returns many
patch or token vectors. Batch limits, supported MIME types, maximum image
resolution, representation kind, scoring operator, and provider usage are
capabilities exposed by the adapter.

The adapter owns provider-specific preprocessing. The workflow owns batching,
leases, retries, accepted-result fencing, persistence, and accounting.

### 4. Projection store

Store multimodal vectors in a physical collection isolated from the current
knowledge, conversation, workflow, and wisdom vector stores. A projection is
addressed by workspace plus a stable projection name and profile fingerprint.

Each row stores one vector and:

- view, source-unit, source-revision, and asset IDs;
- modality, view kind, and group ID;
- ACL/filter labels required before material is returned;
- projection generation and authoritative source watermark;
- active/tombstoned state.

The vector projection may be dropped and rebuilt without changing graph or
source events.

### 5. Retrieval orchestrator

The orchestrator embeds the query in the appropriate modality, searches the
matching projection, groups views into source-level candidates, applies ACL and
scope policy, expands through graph relationships, and fuses optional lexical
or knowledge-index results.

The semantic lens receives authoritative graph/source IDs and selection
reasons. It does not receive an anonymous vector result disconnected from
provenance.

## Embedding Space Profile

The existing core `EmbeddingProfile` captures provider, model, dimension,
metric, and endpoint identity. Native multimodal compatibility additionally
depends on the complete input transformation. Define a versioned
`MultimodalProjectionProfile` containing:

- provider, model, immutable model revision, and endpoint fingerprint;
- declared input modalities and shared output-space identifier;
- dimension, similarity metric, and vector normalization;
- image decoder, colorspace, resize, crop, and tiling policy;
- text tokenizer, truncation, query/document prompt, and pooling policy;
- single-vector or multi-vector representation and aggregation algorithm;
- adapter implementation and profile schema versions.

Its canonical fingerprint becomes the model identity supplied to the physical
store's existing profile guard. Credentials and signed URLs are excluded.

Two vectors are comparable only when their complete projection fingerprints
match. Matching dimensions are not sufficient. Index startup must fail before
writes if the configured profile differs from the registered projection.

Query and document encoders may use different declared prompts or towers, but
that asymmetric pairing is one immutable profile. Operators cannot change one
side independently after indexing.

## Source-Specific Ingestion

### Standalone images

Capture the original image as an immutable asset. Validate MIME by content,
pixel count, dimensions, decoder safety, and configured limits. Preserve EXIF
and supplied metadata only after sanitization.

Create a `whole_image` view for ordinary images. Add `image_region` views only
when a deterministic detector, document layout stage, explicit user crop, or
validated model output identifies useful regions. Blind fixed-grid tiling is
not the default because it multiplies cost and often removes context.

The whole image and regions share a group ID. Retrieval returns the best region
while retaining the whole-image occurrence for display and grounding.

### PDF files

Treat each PDF as a source bundle:

- preserve the original PDF asset;
- extract selectable text into grounded `text_passage` views;
- rasterize pages into `pdf_page_image` views;
- extract or detect meaningful figures/tables into `pdf_figure` views;
- preserve page number, bounding boxes, reading order, and parent page links.

Digital PDFs usually benefit from both text-passage and page-image embeddings.
The text view captures exact wording; the page view captures layout, diagrams,
and visual relationships. Scanned PDFs may initially provide only page-image
views and can later gain OCR text views without changing source identity.

Do not embed one whole multi-page PDF as one vector. Group view-level hits by
page, figure, section, and document after nearest-neighbor search.

Reuse `kg-doc-parser` PDF rasterization, OCR artifacts, normalized pages,
bounding boxes, and source units where they fit. Native image embedding is a
new projection stage, not a replacement OCR workflow.

### Complex page decomposition

A page can contain several photographs, diagrams, tables, plots, and time-series
charts. Preserve them as separate source units instead of treating the page
raster as the only retrievable object:

1. run layout detection over the immutable page render;
2. classify and validate regions such as text, figure, table, chart, formula,
   caption, and legend;
3. assign stable region IDs from source revision, page, region kind, normalized
   bounds, and content hash;
4. create one regional image view per accepted visual object;
5. create structured/text views where a table or chart extractor succeeds;
6. retain `contains`, reading-order, caption, legend, and nearby-text links;
7. also keep a page-level image view so regional crops do not lose layout
   context.

Overlapping detector outputs are deduplicated or represented as parent/child
regions. A table can have an image view, a cell-structure artifact, and a text
serialization under one group ID. A time-series chart can have an image view,
axis/legend regions, and extracted series data. Extracted values are model or
tool derivations and retain their own confidence; they do not replace the
original chart evidence.

Page-level and region-level hits are grouped with bounded sibling contribution
so a page with many detected objects does not dominate retrieval by vector
count alone.

### Plain text and Markdown

Split text using the existing grounded page-index/source-map rules. Send
bounded `text_passage` views through the multimodal model's text encoder.

Plain text can therefore participate directly in cross-modal search: a text
passage can retrieve a related image, and an image query can retrieve the
passage. Keep exact text spans and excerpts as authoritative evidence.

The existing text knowledge index remains useful and may outperform a joint
model for fine-grained conceptual questions. Query policy can search both
spaces and fuse ranks; it must not compare their raw similarity scores.

### Webpages containing images

Capture a webpage as an immutable revision bundle containing:

- canonical/final URL and fetched HTML snapshot;
- normalized visible DOM text and grounded passages;
- each accepted image asset, including selected `srcset` variant;
- image occurrence records with DOM path, nearby heading, caption, alt text,
  link target, and document order;
- optional page screenshot for layout evidence.

Embed visible text as `text_passage` views and image bytes as
`web_image_occurrence` views. The same physical image reused in multiple places
gets one asset but multiple occurrence views or context links. Decorative,
tracking, tiny, hidden, and disallowed cross-origin images are filtered by an
explicit policy.

Publisher-authored alt text and captions are text evidence from the HTML
snapshot; they are not descriptions generated from the image. DOM locators are
validated against the captured revision, not a live page that may have changed.

Web capture extends the existing SSRF, redirect, credential, and byte-size
controls with per-asset limits, total bundle limits, MIME sniffing, pixel
limits, and a maximum subresource count.

### Other multimodal document types

The architecture generalizes through a `SourceAdapter` capability rather than
format-specific graph models. An adapter declares supported MIME types and
implements:

```text
inspect -> source metadata
decompose -> typed source units and containment/order links
build_views -> embedding inputs
validate_locator -> revision-bound evidence validation
resolve_view -> authorized bytes/text for embedding or answering
render_hint -> workbench presentation metadata
```

Slides, office documents, email with attachments, e-books, maps, medical image
series, and future audio/video sources can reuse the same asset, occurrence,
source-map, grounding, projection, and retrieval contracts. A new format should
normally add an adapter and locator subtype, not new node and edge classes.

## Two-Stage Persistence And Scheduling

Native media embedding should use two-stage persistence:

### Stage 1: authoritative capture

- persist source identity, revision, assets, occurrences, and retrieval views;
- append graph/source events and enqueue projection jobs atomically;
- make the source inspectable by ID, metadata, and graph relationships;
- report `captured_not_embedded` rather than claiming semantic readiness.

### Stage 2: embedding projection

- claim bounded batches grouped by profile and modality;
- resolve authorized internal payloads;
- call the text or image encoder;
- accept one fenced result while the lease remains valid;
- persist vectors and advance the projection watermark;
- mark individual views ready, failed, unsupported, or dead-lettered.

Long image batches renew leases only while decode/upload/provider progress is
observable. A late worker cannot overwrite an accepted vector batch. Retry
uses completed view IDs and transform fingerprints so successful embeddings
are not paid for twice.

Text and image batches may use separate worker pools because payload size,
latency, GPU memory, and provider quotas differ. They still write into the same
profile-bound shared projection.

## Query Semantics

### Text query

Embed the query with the profile's text query encoder. Search all active text
and image views in the shared space. This enables direct text-to-image and
text-to-page retrieval.

### Image query

Validate and decode the ephemeral image, embed it with the image query encoder,
and search text and image views. The query image is not persisted unless the
user explicitly invokes `ingest`.

### Mixed text and image query

Do not average vectors by default. Produce one query vector per part, retrieve
independently, and fuse the ranked candidates with deterministic reciprocal
rank fusion. A provider-native joint composition method may be used only when
it is declared and fingerprinted in the profile.

### PDF or webpage query attachment

Normalize the attachment into bounded text/image query views, enforce a query
fanout budget, retrieve per view, then group results. Query-time normalization
is ephemeral and cannot create source or knowledge entities implicitly.

### Candidate aggregation

Multiple views may point to one page, image, source section, or graph entity.
Aggregate after retrieval using a declared policy such as maximum view score
plus bounded corroboration. Cap sibling contributions so a heavily tiled page
cannot dominate results merely by having more vectors.

### Cross-space fusion

The multimodal projection, existing text knowledge index, lexical search, and
graph-neighborhood retrieval are separate ranked sources. Fuse normalized
ranks and retain per-source explanations. Never compare or add raw distances
from different profiles.

## Grounding And Answering

Embedding similarity is candidate discovery, not evidence. Every returned
view resolves to:

- source document and immutable revision;
- asset hash and logical occurrence;
- exact text span, page/region, or whole-image locator;
- active ACL and provenance state;
- embedding projection/profile that selected it.

A multimodal answer model may receive the top authorized images or page crops
directly. A text-only answer model receives grounded OCR, captions, alt text,
or other approved text views when available. The response records which form
the answer model actually inspected.

Claims inferred visually should cite the image/page locator and model-derived
reasoning artifact. A vector match alone cannot support a factual assertion.
Existing controlled mutation remains:

```text
retrieve -> inspect evidence -> propose -> validate -> confirm -> apply
```

Hyperedges are useful for evidence that spans several modalities, such as a
figure, its legend, and an explanatory paragraph. Each endpoint keeps its own
source locator; the hyperedge does not collapse them into one synthetic span.
Higher-order claims may in turn cite that hyperedge and other grounded entities
through a revision-pinned evidence pack. The answering path must expose both
the immediate entity support and the terminal source locators; it must not
present transitive graph proximity as provenance.

## Backend Design

### Chroma

Use a dedicated persistent collection or isolated persistence directory for
each active multimodal projection profile. Store one vector row per retrieval
view and filter by workspace, lifecycle, modality, and ACL-safe labels.

Do not reopen an existing text collection with a multimodal function. The
profile registry must bind the complete multimodal projection fingerprint
before Stage 2 starts.

### PostgreSQL and pgvector

Use dedicated multimodal projection tables or an isolated schema/table bundle.
The existing shared graph vector columns are not suitable for profiles with a
different dimension or model meaning. Index the vector column with the metric
declared by the profile and keep source/group/filter columns available without
joining arbitrary graph payloads before nearest-neighbor selection.

### In-memory tests

Provide a deterministic fake joint encoder whose text and image fixtures map
to the same small vector space. This validates orchestration and cross-modal
ranking without a provider or GPU.

### Single-vector and late-interaction projections

The protocol supports both vector cardinalities from the beginning, while an
implementation may deliver the pooled path first:

- `single_vector`: one fixed-size vector per retrieval view, suitable for
  pooled Sentence-Transformers and CLIP-family adapters;
- `late_interaction`: one embedding set per view, suitable for ColQwen,
  ColPali, and related document models that retain page-patch vectors and score
  them against query-token vectors with MaxSim-like aggregation.

Current Kogwistar nodes, edges, documents, and backend queries expose one
`Sequence[float]` per entity. They can be reused for the single-vector path but
do not natively implement late interaction. Multi-vector support belongs in the
separate multimodal projection abstraction, not in `Node.embedding` and not in
`Grounding`.

A backend-neutral multi-vector store needs at least:

```text
upsert_embedding_set(view_id, vectors, profile, metadata)
delete_embedding_set(view_id, generation)
search_embedding_sets(query_vectors, operator=maxsim, filters, limit)
fetch_embedding_set(view_id)
```

For pgvector, a child table can store `(view_id, vector_ordinal, embedding)` and
an optional coarse vector can shortlist candidates before exact MaxSim over all
vectors for each candidate. For Chroma, storing each patch as an independent
row is possible, but taking only globally nearest patches gives incorrect
document scores. The reference adapter uses a bounded full-row read followed
by exact grouped reranking; production deployments need bounded candidate
generation followed by exact grouped reranking from a side store, or a
purpose-built late-interaction index.

The projection capability advertises supported representation and scoring
operators. Startup fails when a late-interaction profile is attached to a
single-vector-only store. Averaging page patch vectors is allowed only as an
explicit coarse candidate projection; it is not the authoritative
late-interaction score.

The application reference now supports text queries, direct image queries,
and deterministic weighted fusion of bounded independent query results. This
makes ColQwen-style models usable without forcing their storage cost on
pooled-model deployments. Provider-free execution and the Chroma adapter are
covered. `scripts/benchmark_multimodal.py` measures the five source-encoding
shapes in deterministic fake mode and can measure a local ColQwen checkpoint;
the fake numbers are API/batching overhead, not GPU performance. ANN candidate
retrieval, pgvector child-table persistence, and production-scale benchmark
coverage remain follow-up work.

## API And Agent Surface

Large media travels through authenticated upload or guarded server-side fetch,
not base64 embedded in MCP arguments.

Application APIs should expose:

- upload initiation/completion or an already captured `asset_id`;
- `ingest`/`reingest` with raw text, asset ID, or allowlisted URI as mutually
  exclusive inputs;
- source inspection with media parts and per-view projection readiness;
- text, image, and mixed query parts;
- bounded result locators and authorized preview URLs;
- projection profile and source watermark in search responses.

MCP and A2A carry asset references or protocol-native file/URI parts and
delegate to the same gateway. OpenAI-compatible input content parts can be
normalized into the same typed query parts. Unsupported media types fail
explicitly; they are not silently discarded by text extraction.

Agent queries do not persist attachments. The agent must call `ingest`
explicitly to capture a source. `propose` and `confirm` remain the only
knowledge mutation capabilities.

## Workbench

The semantic lens includes retrieval-view IDs and source locators while graph
nodes remain normal Kogwistar entities. The evidence panel can show:

- original or thumbnail image with highlighted region;
- PDF page and figure crop;
- webpage image in captured DOM context;
- exact text passage;
- similarity source, profile, rank, and graph-expansion explanation.

Media is loaded through ACL-checked short-lived URLs and removed when its lens
item is evicted. Sigma/Graphology receives thumbnails or display metadata only,
not original media bytes or high-dimensional vectors.

## ACL, Privacy, And Abuse Controls

Apply authorization before returning source metadata, previews, or text/image
payloads to answer models. Vector search is workspace scoped and candidate
results are filtered through source and occurrence ACLs before graph expansion.
Where post-filtering could produce unacceptable existence leakage, use separate
collections or security partitions appropriate to the deployment.

Provider requests follow data-residency and model-routing policy. Sensitive
images must not be sent to a hosted embedding endpoint merely because text
embedding is allowed there. Record the provider and region in operational
lineage without logging media content or signed URLs.

Enforce MIME sniffing, decompression limits, image pixel limits, PDF parser
sandboxing, webpage subresource limits, timeout/budget controls, and malware
scanning hooks. Strip active webpage content before preview.

## Archive, Restore, And Migration

Portable archives contain source bundle manifests, immutable assets or a
verified external asset inventory, graph events, and retrieval-view metadata.
Multimodal vectors and ANN indexes remain derived state.

Archive manifests record every projection profile and watermark for inspection
but portable restore rebuilds vectors unless an exact compatible backend
snapshot is explicitly selected. Missing source bytes make multimodal
re-embedding impossible and therefore fail restore validation when those
assets are part of the authoritative bundle.

Model changes use blue-green projection migration:

1. register a new isolated profile and projection generation;
2. re-embed all active views from authoritative assets;
3. run cross-modal retrieval evaluation and completeness checks;
4. optionally dual-read and compare results;
5. atomically switch the active named projection;
6. retain or delete the old projection according to rollback policy.

Never resize vectors or overwrite a projection in place. A same-dimension
model change is still a different semantic space.

## Observability And Cost

Trace capture, view generation, queueing, payload resolution, preprocessing,
provider calls, vector persistence, grouping, and reranking separately.
Useful fields include:

- workspace/source/revision/asset/view/job and worker IDs;
- modality, MIME, input dimensions or text tokens, and group size;
- profile and projection fingerprints;
- queue, decode, provider, persistence, and total durations;
- batch size, cache hit, retries, lease age, and accepted/discarded result;
- provider-reported or estimated token/image-unit cost provenance;
- projection watermark, pending/ready/failed/dead-letter counts.

Do not put image bytes, source text, captions, signed URLs, or sensitive EXIF in
ordinary logs or OpenTelemetry attributes.

## Ownership And Required Abstractions

| Concern | Owner |
| --- | --- |
| Source bundle, asset/occurrence lifecycle, APIs, ACL, retrieval policy | LLM-Wiki |
| PDF rasterization, OCR, normalized pages/regions, semantic parsing | Existing `kg-doc-parser` contracts |
| Multimodal embedding provider adapters and projection orchestration | LLM-Wiki, with reusable typed protocols kept narrow |
| Physical vector profile guard and named-projection CAS | Existing Kogwistar core abstractions |
| New generic multi-vector storage, if later required | Separate Kogwistar proposal with Python/Rust parity |
| Evidence rendering | LLM-Wiki workbench |
| Obsidian media projection | Optional sink work, outside the initial capability |

The single-vector implementation can use current backend primitives through an
isolated app-owned engine/projection adapter. The existing text
`EmbeddingFunctionLike` should not be weakened into an untyped multimodal
callable. If multiple Kogwistar products need typed cross-modal or multi-vector
search, promote the proven protocol to core in a separate change.

### Minimal compatibility change set

| Existing primitive | Proposed treatment |
| --- | --- |
| `Node` | No shape change; semantic and source-unit nodes remain nodes |
| `Edge` and multi-endpoint edges | No shape change; use for domain, containment, caption, and navigation relationships |
| `Span` | No breaking change; remains canonical text evidence |
| `Grounding` | Add optional typed `source_refs` and `evidence_pack_refs`; require direct evidence or a validated upstream closure to direct evidence |
| `Document.source_map` | Accept a typed revision-bound source-map superset while adapting legacy chunk dictionaries |
| `Node.embedding`/`Edge.embedding` | Remain single-vector text/graph fields; do not store media embedding sets here |
| `EmbeddingProfile` | Guard the physical store with the canonical multimodal projection fingerprint |
| Backend vector query | Reuse for pooled vectors; add a separate projection capability for late interaction |
| `LogicalRef` | Reuse to locate nodes, edges, and current evidence-pack nodes; pair with revisions/watermarks for immutable grounding |
| `EvidencePackDigest` | Reuse typed node/edge sets and canonical hashing; add revision, composition, role, and source-closure fields through its versioned extensibility contract |

This is smaller and safer than introducing multimodal node/edge subclasses or
turning grounding into a separate hypergraph. The core-shaped semantic changes
are additive `Grounding.source_refs` and `Grounding.evidence_pack_refs`
contracts plus persistence validation. Evidence packs reuse the existing
provenance primitive instead of adding loose node/edge grounding lists. A
compatibility release can deserialize all old payloads unchanged.

## Delivery Plan

### Phase 0: Contracts and evaluation set

- Define source bundle, asset occurrence, retrieval view, locator, and complete
  multimodal projection profile contracts.
- Add the backwards-compatible `Grounding.source_refs` and
  `Grounding.evidence_pack_refs`, source-map adapter, and persistence-boundary
  grounding validation contract.
- Version the evidence-pack extension for pinned logical references, explicit
  composition, evidence roles, source watermarks, and terminal closure hashes.
- Build a small grounded evaluation corpus containing standalone images,
  digital/scanned PDFs, plain text, and webpages with reused/decorative images.
- Add a deterministic fake joint encoder and expected text-to-image,
  image-to-text, image-to-image, and text-to-text rankings.

### Phase 1: Image and plain-text shared space

- Add authenticated content-addressed asset capture.
- Add typed text/image encoder adapters and isolated projection storage.
- Implement Stage-1/Stage-2 lifecycle, profile guard, workers, and status.
- Add text/image/mixed query, candidate grouping, graph expansion, and evidence
  display.

### Phase 2: PDF

- Connect existing parser PDF rasterization and normalized page artifacts.
- Index text passages, page images, and validated figure regions.
- Add page/figure grouping, citations, and PDF workbench rendering.

### Phase 3: Image-bearing webpages

- Add bounded static webpage snapshot and subresource capture.
- Preserve DOM occurrence context and deduplicate physical image assets.
- Index webpage text and image occurrences, then add captured-context display.

### Phase 4: Production hardening and backend expansion

- Benchmark quality, latency, storage, and cost against text-only retrieval.
- Add blue-green profile migration and archive/restore coverage.
- The reference Chroma adapter now provides exact grouped MaxSim under a
  configured scan bound. Add ANN candidate retrieval and a pgvector child-table
  adapter only after measured corpus scale justifies their storage and query
  complexity.

## Acceptance Criteria

- A text query can retrieve a relevant image, PDF page/figure, webpage image,
  and plain-text passage from one declared shared multimodal projection.
- An image query can retrieve relevant images and text passages without first
  converting the query image to a caption.
- Mixed queries are deterministic, bounded, and explain how ranks were fused.
- Every vector result resolves to an immutable source revision and exact source
  occurrence; vectors never serve as factual provenance.
- Nodes and edges can carry direct media evidence through validated
  `Grounding.source_refs` while all existing span-only payloads still round-trip.
- A higher-order node or edge can be grounded by several nodes, ordinary edges,
  and hyperedges through one immutable evidence pack, while every factual
  support path terminates in verified direct source evidence.
- Topology endpoints and evidentiary references remain distinguishable in API,
  persistence, retrieval explanations, and the UI.
- Digital and scanned PDFs preserve page/region grounding and do not collapse
  the whole document into one vector.
- Pages containing multiple images, tables, diagrams, or time-series charts
  produce separately addressable units plus a page-level context view.
- Web image reuse deduplicates bytes without losing occurrence-specific context.
- Stage 1 remains inspectable when Stage 2 is delayed or unavailable, and
  readiness never claims the source is embedded prematurely.
- Profile mismatch, preprocessing drift, and model revision changes fail before
  projection writes.
- Knowledge, conversation, workflow, wisdom, and multimodal projection stores
  cannot accidentally mix incompatible vectors.
- Re-ingestion creates a new source revision and reuses only asset/view vectors
  whose hashes and full projection profile still match.
- Archive/restore can rebuild the complete projection from verified source
  assets and manifests.
- Local and external asset stores behave identically through the resolver, and
  embedding/answer calls receive checksum-verified source views.
- Existing text ingestion, text query, controlled mutation, and Obsidian
  behavior do not regress.

## Required Tests

- Deterministic fake joint-encoder cross-modal ranking in memory.
- Profile tests covering model revision, supported modalities, text prompts,
  image resize/crop, normalization, dimension, and metric drift.
- Stage-1 capture followed by delayed, resumed, failed, and retried Stage-2
  embedding.
- Lost-lease and duplicate-worker tests proving one accepted vector result.
- Standalone image whole/region grouping and locator integrity.
- Digital and scanned PDF text/page/figure ingestion and grouped retrieval.
- Multi-object page tests covering separate images, tables, chart/legend/axis
  regions, structured table data, and bounded sibling-score aggregation.
- Plain-text passage retrieval from an image query.
- Webpage HTML snapshot, image occurrence context, byte deduplication, and
  decorative/subresource filtering.
- Re-ingestion with unchanged and changed assets.
- ACL isolation and cross-workspace provenance rejection before media return.
- MCP, HTTP, OpenAI-compatible, and A2A attachment-reference contracts.
- Archive missing-asset, checksum, exact restore, and full re-embedding tests.
- Local-filesystem and fake external-object-store `AssetResolver` contract
  tests, including byte-range reads, checksum failure, ACL denial, and cleanup.
- Higher-order node and edge tests whose `all_of` pack cites multiple nodes, an
  ordinary edge, and a multi-endpoint/edge-to-edge hyperedge.
- Evidence-pack tests for explicit `all_of` and `any_of`, typed ID separation,
  revision/watermark pinning, deterministic closure hashing, and archive replay.
- Grounding dependency tests rejecting self-reference, future references,
  cycles, hash tampering, cross-workspace references, and ACL laundering.
- Upstream tombstone tests preserving historical reproducibility while marking
  the current higher-order derivation stale or review required.
- Chroma and pgvector isolated-projection integration tests.
- Pooled Sentence-Transformers-style and ColQwen-style multi-vector contract
  tests, including exact MaxSim, coarse-candidate reranking, and rejection by a
  single-vector-only store.
- Browser tests for image regions, PDF pages, and captured webpage context.
- Blue-green rebuild, dual-read evaluation, atomic cutover, and rollback.

## Rejected Alternatives And Traps

- Replacing all knowledge embeddings with one multimodal model: source evidence
  retrieval and curated knowledge retrieval have different semantics and may
  need different models.
- Mixing native image vectors into existing graph vector tables: matching
  dimensions do not establish a shared semantic space.
- Using only whole-document vectors for PDFs or webpages: produces poor
  grounding and hides which page, passage, or image matched.
- Treating image alt text or captions as the image vector: loses native
  image-to-image and image-to-text behavior.
- Treating vector similarity as provenance: a nearest neighbor is only a
  candidate.
- Treating graph endpoints or neighborhood membership as grounding: topology
  does not prove the relation it represents.
- Storing loose node and edge IDs directly on a higher-order entity: this loses
  namespace, revision, composition, integrity hash, and source-closure checks.
- Averaging mixed-query vectors without a model-declared composition rule:
  changes query meaning unpredictably.
- Persisting query attachments automatically: turns reads into hidden source
  mutations.
- Supporting multi-vector models through accidental averaging: discards their
  late-interaction semantics while retaining their cost.
- Reusing vectors after preprocessing changes: image resize and text prompt
  changes can alter the space even when provider/model/dimension are unchanged.

## Open Decisions Before Implementation

- Select the initial shared-space model/provider and verify its license,
  deployment, batching, image-size, and cross-modal quality constraints.
- Select the object-store abstraction and authenticated upload protocol.
- Decide whether the first projection is global per workspace or partitioned
  further for high-security ACL domains.
- Choose PDF figure detection and webpage capture policies.
- Establish result grouping weights and a representative retrieval benchmark.
- Decide whether provider-native mixed-query composition is needed in version
  1 or rank fusion is sufficient.
- Set limits for image pixels, PDF pages, webpage subresources, query fanout,
  vector count, and retained old projection generations.

## Operating Settings Console

The workbench settings console displays the effective text and multimodal
profiles, backend compatibility, Qwen3-VL service readiness, and projection
state. Local Chroma text embeddings remain the default knowledge plane while
the Docker Qwen3-VL service is an optional multimodal route.

The route can be enabled or disabled after confirmation without changing its
profile. Model, provider, metric, dimension, preprocessing, or representation
changes remain staged until a graceful restart and isolated re-embedding are
completed. The console never performs in-place vector migration or exposes
service credentials.

## Technical References

- [ColPali paper: efficient document retrieval with vision-language models](https://arxiv.org/abs/2407.01449)
- [ColPali/ColQwen reference implementation](https://github.com/illuin-tech/colpali)
- [Sentence Transformers image-search example](https://www.sbert.net/examples/sentence_transformer/applications/image-search/README.html)
