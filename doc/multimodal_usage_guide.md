# Multimodal Usage Guide

Multimodal retrieval is an optional projection beside the canonical source and
knowledge graphs. It stores revision-bound source views and profile-scoped
vectors; similarity results do not become graph truth automatically.

## Lifecycle

```text
canonical source revision
        |
        +--> text/parser graph
        |
        +--> multimodal capture (Stage 1)
                    |
                    +--> authorized asset resolver
                    +--> profile-specific embedding (Stage 2)
                    +--> vector projection
```

1. Ingest the canonical source through the existing `ingest` operation.
2. Capture revision-bound multimodal units with `multimodal_capture`.
3. Promote pending units with `multimodal_index`.
4. Search with `multimodal_search` or opt into multimodal results on `search`
   and `query`.
5. Resolve and authorize source-map references before using a result as model
   context.

The source revision, source namespace, locator, and media digest are part of
the evidence identity. Binary assets must be resolved from an application-owned
asset store; callers must not provide arbitrary filesystem paths or executable
resolvers.

## Python Usage

The application API is useful for ingestion workers and local integration
tests:

```python
pipeline.capture_multimodal_source(
    workspace_id="demo",
    source_id="lecture-1",
    source_revision_id="lecture-1@rev-3",
    source_format="markdown",
    raw_text=text,
)

pipeline.embed_multimodal_pending(
    batch_size=8,
    resolver=asset_resolver,  # required for binary content_ref units
)
```

For an image region, create a `MultimodalSourceUnit` with a normalized typed
locator and an immutable `content_ref`. The resolver supplies the actual bytes
so Stage 2 can calculate `asset_sha256`.

## MCP

The MCP server exposes these operations:

```text
multimodal_capture   write Stage 1 source units
multimodal_index     promote pending units
multimodal_search    authorized text- or image-to-multimodal search
multimodal_status    readiness, profile, and stage counts
```

The existing `query`, `search`, and `hypergraph_search` tools accept:

```json
{
  "retrieval_mode": "auto",
  "include_multimodal": true,
  "multimodal_limit": 8,
  "retrieval_required": false
}
```

Multimodal retrieval is opt-in on these combined tools. A multimodal failure
is reported separately unless `retrieval_required` makes retrieval failure
fatal.

For an image query, send an authorized `image_content_ref` to
`multimodal_search`. The server resolves it through the host-owned asset
resolver; callers never send a filesystem path or arbitrary URL directly.

## REST

The transport-neutral workbench exposes:

```text
POST /api/multimodal/capture
POST /api/multimodal/index
POST /api/multimodal/search
GET  /api/multimodal/status?workspace_id=demo
```

`POST /api/ask` and `GET /api/lens` retain their existing contracts and accept
the optional `include_multimodal` and `multimodal_limit` fields. `/api/lens`
also accepts the semantic retrieval controls used by MCP.

## Profile Isolation

Every model, preprocessing configuration, dimension, metric, and serving
revision produces a profile fingerprint. Do not compare or combine scores from
different fingerprints, even when dimensions match. Replacing a model requires
a new isolated projection and re-embedding.

## Result Semantics

Multimodal hits contain source and revision identity, a locator, profile
fingerprint, and dereference status:

```text
available   source-map and optional targets are authorized and current
stale       the pinned source revision is no longer current
unauthorized the request cannot access the source or target
unresolved  no authorization-aware dereferencer was supplied
```

A high vector score is evidence for review, not permission to create a semantic
edge. The canonical graph remains authoritative.

## Sidecar And Workflow

The sidecar is a bounded retrieval adapter, not a second graph and not an
autonomous agent. Its lifecycle is represented by the ordinary workflow design
`retrieval.multimodal_sidecar.v1`:

```text
dispatch -> text graph + sidecar -> checkpoint -> authorize
         -> assimilate -> degraded or finalize
```

Sidecar subscriptions must have a stable run ID, subscription ID, timeout,
event limit, and current-hit validator. `drain()` is the final authorization
and stale-result boundary.

## Smoke Test

A useful deterministic smoke run should verify:

```text
text source ingestion
image-region capture
Stage 2 embedding
graph-only search
multimodal-only search
combined search
source revision dereference
ACL rejection
profile mismatch rejection
embedding-service degradation
```

Required CI uses deterministic fake encoders. Live Ovis/Qwen tests are optional
and must be enabled explicitly; fixture vectors must never be presented as real
model accuracy or latency measurements.
