# Vector Backend Adapters

LLM-Wiki keeps the default installation lean and uses PostgreSQL in the
multi-process Compose deployment. Pinecone and Qdrant are optional Kogwistar
backend adapters selected at runtime:

```text
--backend postgres   # default durable Compose backend
--backend chroma     # local persistent single-process backend
--backend pinecone   # optional kogwistar-pinecone package
--backend qdrant     # optional kogwistar-qdrant package
```

Install only the provider needed by the deployment:

```text
pip install "kogwistar-llm-wiki[vector-pinecone]"
pip install "kogwistar-llm-wiki[vector-qdrant]"
pip install "kogwistar-llm-wiki[vector-all]"
```

The normal image does not include the two external SDKs. This avoids forcing
credentials, network clients, and transitive dependencies on users who use
PostgreSQL or Chroma. A future all-adapters image can install the `vector-all`
extra only after the adapter packages have immutable released versions.

The Dockerfile exposes the same opt-in without changing the normal image:

```text
docker build --build-arg LLM_WIKI_VECTOR_EXTRAS=vector-all -t llm-wiki:all-backends .
```

That local build pulls released adapter packages from the configured package
index. The versioned release workflow instead passes immutable merged adapter
commit SHAs to the Dockerfile, so it does not depend on a moving feature
branch or a package-index upload completing at the same time.

Versioned Docker releases are gated by the all-adapters build. The release
workflow builds this variant without publishing it, verifies both optional
adapter imports, and only then publishes the standard release image. If an
adapter package is missing or incompatible, the release job stops before any
release image is pushed.

## Configuration

Pinecone requires `PINECONE_INDEX_HOST` and `PINECONE_API_KEY`. Qdrant accepts
either `QDRANT_URL` for a remote service or `QDRANT_PATH` for a persistent
local store. The latter must point at a mounted durable directory in a
container deployment; an unconfigured Qdrant backend is rejected rather than
silently using an in-memory store.

Both adapters are lazy-loaded. Selecting PostgreSQL or Chroma does not import
either optional SDK. The adapters use Kogwistar's existing backend injection
contract and preserve graph-space namespaces through the adapter prefix.

Pinecone and Qdrant are eventual, non-transactional vector projections. They
must not be used where a workflow requires PostgreSQL's atomic replacement
semantics. Canonical metadata, provenance, and application safety fences stay
under the existing LLM-Wiki/Kogwistar paths.
