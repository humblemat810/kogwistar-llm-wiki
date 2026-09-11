# ADR: Grounded Multimodal Knowledge Without Multimodal Embeddings

- Status: Proposed
- Scope: LLM-Wiki source ingestion, provenance, retrieval, agent APIs, archive,
  and workbench presentation
- Related: `adr_interactive_graph_explorer.md`,
  `adr_agent_gateway_and_otel.md`, `adr_archive_recovery.md`,
  `adr_conversation_two_stage_materialization.md`

## Context

LLM-Wiki is currently a text-first product. `IngestPipelineRequest` accepts one
`raw_text` value, source revisions are hashed from that text, the app invokes
the text parser path, MCP ingestion accepts text or a fetched text URI, and the
workbench displays grounding as document excerpts.

The repositories nevertheless contain useful multimodal foundations:

- `kg-doc-parser` can rasterize PDFs, process page images through a resumable
  OCR workflow, and retain page artifacts.
- Its workflow-ingest contract already models OCR text, image regions, pure
  images, pages, bounding boxes, and stable source units.
- `normalize_ocr_pages` preserves non-text regions and labels their intended
  embedding space, although the current semantic path does not make those
  regions first-class knowledge evidence.
- Kogwistar documents can hold OCR dictionaries, and graph entities can carry
  modality-neutral metadata and lineage.
- LLM-Wiki already has source revisions, immutable event history, scoped graph
  spaces, maintenance jobs, controlled proposals, embedding profiles, and
  portable archives.

The missing capability is therefore not a new graph database or a single
multimodal model. It is a product-level contract joining immutable media,
typed evidence locations, versioned media-to-text derivations, semantic
parsing, retrieval, authorization, and presentation.

This design must remain useful when no multimodal embedding model is
available. A deployment using only the existing text embedder should still be
able to ingest, search, cite, maintain, archive, and display images, scanned
PDFs, audio, and video. Native image or audio embeddings may improve recall
later, but they must not become authoritative or required.

## Decision

Adopt a **media asset -> grounded source units -> textual derivatives ->
knowledge graph** architecture.

Raw media and its content hash are authoritative source evidence. OCR,
transcripts, captions, detected objects, table reconstructions, and summaries
are versioned derived artifacts. Existing text embedding and graph retrieval
operate on those textual derivatives. Every derived claim retains a resolvable
lineage chain back to the exact source revision and media locator.

```text
immutable media asset
    -> source revision and media manifest
    -> typed source units (page, region, time range, frame)
    -> versioned text/structure derivatives
    -> existing semantic parser and maintenance workflows
    -> knowledge nodes, edges, and hyperedges
    -> text embeddings and graph projections
```

Media bytes do not belong in graph events, vector columns, prompts, or node
metadata. They live in an app-owned content-addressed asset store. Graph state
contains stable IDs, hashes, metadata, locators, and lineage references.

## Why Textual Derivatives Are The Baseline

Multimodal support and multimodal vector search are separate capabilities.
The baseline retrieval path needs no multimodal embedding model:

| Source | Searchable embedding | Precise evidence locator |
| --- | --- | --- |
| Digital PDF | Extracted text, headings, tables | Page and character span |
| Scanned PDF | OCR text and layout description | Page and bounding box |
| Image | OCR, metadata, object labels, caption | Whole image or bounding box |
| Audio | Transcript, speaker turns, metadata | Half-open time range |
| Video | Transcript, scene/frame descriptions | Time range plus optional frame region |
| Diagram or chart | OCR labels, legend, structural description | Region IDs and bounding boxes |

A normal text query embeds against these embeddings. Graph expansion,
metadata filters, exact IDs, source links, and lexical matching supplement
vector recall. For example, an image of a circuit can be found through its
caption, OCR labels, component observations, nearby document text, or links
from known entities without ever embedding the image pixels.

This baseline is preferable to pretending that text and image vectors share a
meaningful distance. Its main limitation is explicit: material omitted by the
derivation cannot be retrieved semantically. Multiple bounded textual views
and transparent derivation quality are therefore more valuable than one
opaque generated caption.

## Source And Evidence Model

### Media asset

Each captured binary has an immutable asset record containing at least:

- asset ID derived from workspace and SHA-256 content hash;
- source document ID and source revision ID;
- original URI, media type, byte length, and original filename when known;
- media properties such as dimensions, page count, duration, and codec;
- content-store locator that is never exposed without an ACL check;
- capture timestamp, uploader/principal, and provenance policy;
- malware/content-validation status;
- retention and archive state.

Re-ingestion preserves the stable source document identity but creates a new
revision from the new bytes. Old assets and their derivations remain immutable
for history and citation resolution.

### Typed source locator

Character spans cannot accurately identify pixels or moments in time. Define
an app/parser boundary DTO with a discriminated locator union:

- `text_span`: page plus half-open `[start_char, end_char)`;
- `page_region`: 1-based page plus normalized bounding box;
- `time_range`: half-open `[start_ms, end_ms)`;
- `frame_region`: time or frame range plus normalized bounding box;
- `whole_asset`: explicit whole-file evidence.

Bounding boxes use normalized coordinates in `[0, 1]`, with origin and axis
orientation stated in the contract. Time and frame ranges are half-open so
adjacent units do not overlap accidentally. Every locator includes the source
revision and asset hash it was validated against.

For the first implementation, LLM-Wiki can persist each locator as a typed
source-unit artifact using existing graph entity metadata and references. A
knowledge claim grounded through OCR or a transcript uses a real Kogwistar
text `Span` in the derivative document and a `derived_from` reference to the
source-unit artifact. It must not fabricate a character span over binary
content. A future generic Kogwistar evidence-locator union should be proposed
only if more than this product needs direct typed media grounding.

### Derived artifact

Every derivative records:

- source revision, input asset hash, and source-unit IDs;
- derivative kind, such as OCR, transcript, caption, table, or scene summary;
- extractor provider, model/tool version, configuration fingerprint, and
  prompt/schema version where applicable;
- output hash, language, confidence/quality, and creation time;
- whether the result is deterministic, machine-observed, model-inferred, or
  human-verified;
- exact parent derivations when one stage consumes another.

Derived artifacts are append-only and replaceable by a newer active version.
They are not silently rewritten when a better OCR or caption model is added.

## Trust And Grounding Semantics

The following distinction is mandatory:

- Raw bytes and verified metadata are source evidence.
- OCR and ASR text are machine observations with measurable alignment.
- Captions, scene descriptions, object interpretations, and chart readings are
  model inferences.
- Semantic knowledge is a further derivation and must cite the observation or
  inference that supports it.

A caption must not be presented as a verbatim source excerpt. Answers should
identify the evidence type, such as "OCR text on page 3", "machine-generated
description of region 2", or "transcript at 01:12-01:18". Low-confidence OCR,
uncertain speaker attribution, or an unverified visual description should
propagate uncertainty into proposals and answers.

Every durable semantic claim must satisfy one of these paths:

```text
claim -> exact text span -> source revision

claim -> derivative text span -> typed media source unit -> source revision

claim -> grounded upstream claim/artifact -> ... -> source revision
```

User-provided media remains user-supplied evidence. It is not automatically
promoted to authoritative knowledge. Existing `propose -> validate -> confirm`
semantics continue to govern controlled changes.

## Ingestion Workflow

Add media handling as explicit resumable stages around the existing parser:

1. Capture or upload the source through an allowlisted URI or authenticated
   upload flow.
2. Stream while enforcing size, type, redirect, decompression, and timeout
   limits; compute SHA-256 before acceptance.
3. Persist the immutable asset and source revision manifest.
4. Inspect media metadata and split it into pages, tracks, scenes, or regions.
5. Run configured derivation adapters: PDF text extraction, OCR, ASR, image
   description, table extraction, and related deterministic tools.
6. Validate source-unit locators and derivative lineage.
7. Assemble a canonical textual document from accepted derivatives while
   retaining a map from every character range to source units.
8. Send that textual document through the existing page-index/layered parser.
9. Persist the parsed graph, readiness state, usage, and maintenance request
   through the existing ingestion path.
10. Materialize text embeddings and projections asynchronously where the
    selected persistence mode permits it.

The workflow state is keyed by source revision plus transform fingerprint.
Completed page, region, or time-slice derivations are reused after interruption.
An in-flight operation may retry; a completed accepted derivation is not called
again. Provider usage is persisted incrementally with model and modality.

The first delivery should integrate LLM-Wiki with the existing
`kg-doc-parser` OCR workflow and `WorkflowIngestInput`/`SourceUnit` contracts.
LLM-Wiki should not introduce another PDF renderer, OCR state database, or
image-region schema.

## Analyzer Configuration

Analysis and embedding are separate scoped configurations:

```text
LLM_WIKI_MEDIA_PDF_EXTRACTOR
LLM_WIKI_MEDIA_OCR_PROVIDER / MODEL
LLM_WIKI_MEDIA_ASR_PROVIDER / MODEL
LLM_WIKI_MEDIA_VISION_PROVIDER / MODEL
LLM_WIKI_MEDIA_MAX_BYTES
LLM_WIKI_MEDIA_ALLOWED_TYPES
LLM_WIKI_MEDIA_STORE
```

The exact names may follow the existing provider configuration style, but an
OCR or vision model must never be confused with the knowledge embedding
profile. The knowledge graph continues to use one compatible text embedding
profile per physical vector space.

Deployments can choose capability tiers:

- Metadata only: type, hashes, dimensions/duration, filenames, and explicit
  user descriptions are searchable.
- Local derivation: deterministic PDF extraction, local OCR, and local ASR.
- Hosted derivation: provider OCR/ASR/vision with explicit usage accounting.
- Optional native vectors: separate image/audio projections described below.

Missing optional analyzers should produce an explicit partial-readiness state,
not an empty "successful" parse.

## Retrieval And Query

Search should retrieve in this order:

1. text-vector and lexical candidates from derivative documents and knowledge;
2. exact metadata matches, source IDs, tags, people, timestamps, and media
   properties;
3. bounded graph expansion through source-unit and derivation links;
4. optional native-modality projection candidates;
5. deterministic rank fusion and authorization filtering.

Do not compare raw scores from different embedding models. If optional
modality projections are enabled, combine ranked lists with reciprocal-rank
fusion or a calibrated reranker and expose why each result was selected.

A media query follows the same adapter path but defaults to ephemeral state:

- an image query becomes OCR, labels, and bounded descriptions;
- an audio query becomes an ASR transcript and metadata;
- a video query becomes transcript plus sampled scene descriptions.

The ephemeral derivatives can search existing knowledge without becoming a
durable source. Persistence requires an explicit ingest action and provenance
policy. This prevents a normal question containing an attachment from silently
changing the wiki.

## Optional Native Modality Embeddings

Native image/audio/video embeddings are a future optimization, not part of the
baseline contract. If added, each is a named, rebuildable projection with:

- its own physical vector collection;
- its own `EmbeddingProfile`, dimension, metric, and model fingerprint;
- source-unit IDs as projection targets;
- a projection watermark and rebuild status;
- no authority to create or mutate knowledge by itself.

Never store image vectors in the current text knowledge vector column merely
because dimensions happen to match. Conversation, workflow, knowledge, and
media projections may use different models only when their physical stores and
profiles are isolated according to the existing embedding-profile invariant.

Deleting a native modality projection must not delete source assets,
derivatives, graph events, or text searchability.

## Agent And HTTP Contracts

Do not overload `raw_text` with base64 or an OCR JSON string. Extend the
application-level source contract with mutually exclusive input forms:

- `raw_text` for the existing text path;
- `asset_id` returned by an authenticated upload;
- an allowlisted `source_uri` for server-side streamed capture.

Large media should use an upload endpoint or object-store upload grant. MCP and
A2A should pass an asset reference or safe URI, not multi-megabyte base64 tool
arguments. Local filesystem paths remain disallowed through remote agent APIs.

The existing agent capabilities remain semantically stable:

- `ingest` and `reingest` accept an asset reference and optional declared media
  type, then call the canonical ingestion workflow;
- `source` reports asset metadata, revision, derivative/readiness states, and
  safe evidence links;
- `query` and `search` may accept an ephemeral attachment reference;
- `status` reports pending/failed media derivations;
- `propose` and `confirm` remain the only controlled mutation path.

OpenAI-compatible message content parts and A2A artifacts can be normalized at
the gateway, but they must delegate to the same upload, authorization, and
derivation services. Unknown content parts fail explicitly instead of being
dropped by the current text-only `_content_to_text` behavior.

## Workbench Experience

The graph remains the navigation surface, while an evidence panel renders the
source form appropriate to the selected grounding:

- PDF page with highlighted region;
- image crop with surrounding context;
- audio player seeking to the cited time range;
- video player seeking to the cited scene/frame;
- transcript/OCR text beside the original media;
- derivation method, confidence, revision, and verification badges.

The browser receives short-lived authorized asset URLs or proxied byte ranges,
never raw storage paths. It should load thumbnails first, cancel media requests
for dropped graph nodes, support keyboard navigation and text alternatives,
and keep full media outside Sigma/Graphology renderer state.

## Storage, ACL, And Security

Recommended storage:

- local development: content-addressed files under an app-owned
  `source_artifacts/blobs/sha256/...` root;
- production: an object store with encryption, retention policy, and
  short-lived signed access mediated by LLM-Wiki;
- graph/events: manifests, hashes, locators, lifecycle, ACL, and lineage only.

Asset, source revision, source unit, derivative, and knowledge ACLs must be
checked together. A user who may see a derived node does not automatically gain
permission to fetch its source media. Search and captions must not leak content
from an inaccessible asset. Logs and OpenTelemetry attributes contain IDs,
hash prefixes, sizes, and timings, not media bytes, signed URLs, transcripts,
or captions by default.

URI capture retains the current private-network/redirect protections and adds
MIME sniffing, archive/decompression limits, image pixel limits, PDF page
limits, audio/video duration limits, and malware scanning hooks. Declared MIME
type alone is not trusted.

## Archive And Recovery

Portable archives must include:

- media manifests and source/derivative lineage events;
- every required immutable asset and derivative payload, with checksums; or
- an explicit externally managed object-store inventory whose retention and
  restore preconditions are verified before graph restore.

The current `source_artifacts` archive directory is the natural app-owned
location, but archive creation must inventory binary files explicitly rather
than assuming text-only source artifacts. Restore validates asset checksums
before replay and fails closed when required bytes are missing. Thumbnails,
text embeddings, and optional modality vectors remain rebuildable projections.

## Observability And Accounting

Each media stage emits workflow and OpenTelemetry events with:

- workspace, source document, revision, asset, unit/page/time range, and job;
- analyzer provider/model/version and transform fingerprint;
- input bytes/duration/pages and output units/characters;
- queue, execution, retry, cache-hit, and validation durations;
- token/cost provenance where a provider reports or permits estimation;
- terminal status, quality summary, and retryable/non-retryable failure class.

Raw content and generated descriptions are excluded from normal trace fields.
Status should distinguish source capture, derivation, semantic parse, graph
persistence, and projection readiness so "searchable by metadata" is not
confused with "fully semantically indexed".

## Ownership

| Concern | Owner |
| --- | --- |
| PDF/image normalization, OCR workflow, page/region DTOs | `kg-doc-parser` |
| Audio/video adapters and normalized source-unit contracts | `kg-doc-parser` when reusable; app adapter until generalized |
| Asset storage, upload, source lifecycle, ACL, orchestration, status | `kogwistar-llm-wiki` |
| Semantic parsing, maintenance, promotion, agent capabilities | Existing LLM-Wiki composition over parser/core primitives |
| Event ordering, graph writes, text spans, namespaces, embedding profiles | Kogwistar core as currently exposed |
| Media evidence UI and authorized asset transport | LLM-Wiki workbench |
| Obsidian media projection | Separate optional sink work; not required for this capability |

The first implementation should not require a Kogwistar core change. If typed
media evidence becomes a shared requirement across Kogwistar applications, a
separate core ADR can generalize `Span` into a backwards-compatible evidence
locator contract with Python/Rust parity.

## Delivery Plan

### Phase 0: Contract and fixtures

- Define media manifest, source-unit locator, derivative, and readiness DTOs.
- Map existing OCR `SourceUnit` and `BoundingBox` contracts into the app source
  graph without fabricating text spans.
- Add tiny deterministic PNG, scanned-PDF, WAV, and short-video fixtures.
- Pin identity, hashing, coordinate, time-range, ACL, and lineage invariants.

### Phase 1: PDF and image ingestion without native media embeddings

- Add authenticated asset capture and content-addressed storage.
- Connect LLM-Wiki ingestion to the existing parser OCR workflow.
- Build canonical derivative text and source-unit mappings.
- Persist and query through the current text embedding profile.
- Add source inspection, archive, and workbench image/PDF evidence views.

### Phase 2: Audio and video

- Add resumable ASR, speaker/segment, frame-sampling, and scene-description
  adapters under the normalized source-unit contract.
- Add time/frame grounding validation and browser playback citations.
- Reuse the same semantic parse, maintenance, query, and archive paths.

### Phase 3: Agent attachment interoperability

- Add upload/asset-reference HTTP APIs.
- Normalize MCP, OpenAI-compatible, and A2A attachment references.
- Add ephemeral attachment query semantics and explicit ingest promotion.

### Phase 4: Optional native modality projections

- Benchmark textual-derivative recall before adding complexity.
- Add isolated profile-guarded image/audio vector projections only where the
  benchmark demonstrates meaningful value.
- Add deterministic rank fusion, projection rebuild, and migration tests.

## Acceptance Criteria

- An image or scanned PDF can be ingested, searched, cited, re-ingested,
  archived, restored, and displayed using only a configured text embedder.
- Audio/video follow the same source lifecycle and produce time-addressable
  citations once their adapters are enabled.
- Every answer citation resolves through an immutable source revision to an
  exact text, page-region, time-range, frame-region, or whole-asset locator.
- No binary content receives a fabricated character span.
- OCR/captions/transcripts identify their derivation method and are never
  represented as original source text.
- Reprocessing with a new analyzer creates new versioned derivatives without
  mutating prior evidence or source revisions.
- Interrupted work resumes per accepted unit and does not repeat completed
  provider calls.
- ACL checks prevent unauthorized retrieval of assets and their derived text.
- Existing text ingestion and query behavior remains backwards compatible.
- Disabling or deleting optional modality-vector projections leaves the wiki
  fully recoverable and text-searchable.
- Provider-free tests exercise the complete pipeline with deterministic fake
  OCR/ASR/caption adapters and real locator/hash validation.

## Required Tests

- Source identity and revision tests based on media bytes rather than generated
  text.
- Locator boundary tests for text, page regions, time ranges, and frame regions.
- Existing OCR workflow integration with accepted-page resume and cache reuse.
- A fake image derivation -> semantic parse -> graph persistence -> query ->
  citation round trip using only the tiny text embedder.
- Equivalent fake audio/video derivative and time-citation round trips.
- Re-ingestion and analyzer-version changes preserving old evidence.
- Failure injection between capture, derivation, parse, persistence, and
  projection stages.
- MCP/HTTP/A2A schema tests proving attachments are referenced, bounded, and
  never silently ignored.
- Cross-workspace provenance and ACL rejection, including derivative-text
  leakage tests.
- Archive checksum, missing-asset, exact restore, and projection rebuild tests.
- Workbench browser tests for image crops, PDF pages, and timed media citations.
- Optional projection tests proving profile isolation and rank fusion without
  comparing raw cross-model distances.

## Rejected Alternatives And Traps

- Storing base64 media in graph nodes or events: bloats replay, telemetry,
  archives, and prompts.
- Treating a generated caption as the source: loses the distinction between
  observation and inference.
- Assigning fake text offsets to a binary: creates unresolvable provenance.
- Requiring a single multimodal embedding model: couples durable knowledge to
  one provider and leaves local/offline deployments unsupported.
- Mixing text and media vectors in one physical collection: violates embedding
  profile and distance semantics even when dimensions match.
- Sending large base64 payloads through MCP tool calls: harms discovery,
  transport limits, retries, and auditability.
- Letting query attachments persist implicitly: turns a read operation into an
  unexpected mutation.
- Reimplementing PDF/OCR handling in LLM-Wiki: duplicates an existing resumable
  parser workflow and creates incompatible source-unit contracts.

## Open Decisions Before Implementation

- Select the production asset-store abstraction and retention contract.
- Decide which deterministic local OCR and ASR implementations are supported
  defaults versus optional extras.
- Specify maximum bytes, pixels, pages, duration, and derivative fanout per
  deployment profile.
- Decide whether externally managed object-store assets may be referenced by a
  portable archive or must always be copied into it.
- Define the minimum confidence/review policy for inferred visual descriptions
  used in promoted knowledge.
- Determine whether first-class media locators remain app-owned or warrant a
  later Kogwistar core proposal after the app contract proves stable.
