# Persistent Embedding Migrations

## Why a migration is required

PostgreSQL pgvector columns have a physical type such as `vector(2)` or
`vector(1024)`. Changing an LLM-Wiki embedding provider, model, or dimension
does not alter an existing column. LLM-Wiki checks the live schema during
backend bootstrap and stops before any graph write if the configured dimension
does not match the stored type.

The current PostgreSQL namespace bundle also shares one set of vector tables
across conversation, workflow, knowledge, and wisdom. Those spaces must use
one provider/model/dimension/base-URL profile. Separate per-space profiles are
supported only by physically isolated stores such as the current Chroma layout.

Persistent Chroma directories use the same core profile contract. Their
conversation, workflow, knowledge, wisdom, and derived-knowledge directories
are separate physical stores and may use different profiles. A populated
directory created before profile registration is not assumed to match the
current configuration; normal startup fails closed until the operator verifies
and explicitly adopts the legacy profile.

## Safe cutover

1. Stop REST, MCP, ingestion, and maintenance writers.
2. Create and verify a portable archive of canonical state.
3. Provision an empty, isolated PostgreSQL database or schema for the new
   embedding profile.
4. Start LLM-Wiki against that target with the new provider/model/dimension.
5. Restore or replay canonical state, then run the normal semantic
   materialization/re-embedding workflow.
6. Verify source, graph, event, and vector counts; run representative searches.
7. Switch serving traffic to the new target and retain the old target until the
   cutover is accepted.

Do not mutate a populated vector column with `ALTER TABLE ... ALTER COLUMN
embedding TYPE vector(N)`. Existing vectors have the old shape and existing
HNSW indexes were built for them. An in-place conversion cannot produce valid
new-model embeddings.

For Chroma, do not replace collection metadata or reuse a directory with
another model. Chroma collection metadata does not establish semantic identity,
and a same-dimensional model can still produce incomparable vectors. Use the
operator-only profile commands to inspect state or attest a known legacy
profile; for a real model change, replay canonical state into a new directory
and rebuild the vector projections.

## Failure message

An incompatible startup reports every affected table and column, for example:

```text
PostgreSQL pgvector schema mismatch: configured embedding dimension is 1024,
but public.gke_nodes.embedding is vector(2). No data was written.
```

The message is a safety stop, not a request to recreate a container. Correct
the target datastore/profile and recreate the app container with the corrected
environment. Rebuilding the image is necessary only when application code or
the Dockerfile changed.

To inspect persistent Chroma profile state without binding a profile:

```powershell
python -m kogwistar_llm_wiki --data-dir ./data --backend chroma `
  embeddings inspect --workspace demo
```
