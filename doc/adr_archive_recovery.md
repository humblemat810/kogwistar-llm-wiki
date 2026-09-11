# ADR: Portable Archive, Recovery, And Chroma Snapshots

## Status

Accepted. The archive implementation is operator-only and does not change the
MCP, REST, or Obsidian contracts.

## Decision

Kogwistar entity events are the authoritative recovery source. LLM-Wiki emits
versioned archives containing lossless event envelopes for every app-owned
workspace namespace. Each namespace has explicit `from_seq` and `to_seq`
watermarks. The archive timestamp is observability metadata; it is not the
correctness cursor.

Archive manifests record the registered embedding profile for each graph space,
including provider, model, dimension, similarity metric, and a sanitized
endpoint fingerprint. Archive creation does not infer a profile from an
embedding callable name. An engine without a registered profile must be
initialized and verified before it can be archived.

The existing five-field replay iterator remains compatible. Archive tooling
uses the separate full envelope containing `namespace`, `seq`, `event_id`,
`entity_kind`, `entity_id`, `op`, canonical `payload_json`, and `created_at`.

Archives may be a complete base archive or a delta whose ranges begin exactly
after a verified parent archive. Events are retained indefinitely and archive
creation never prunes history. A timestamp can select the newest completed
archive at or before that time, but an arbitrary live multi-namespace stream is
never truncated at a wall-clock timestamp.

## Backend Semantics

Chroma is a derived two-stage projection. Its canonical metadata and event
state can be archived and replayed, and vectors/indexes can be rebuilt. A
Chroma filesystem snapshot is only captured while writers are quiescent and
must include the complete application persistence directories, not one
collection directory. Backend snapshots are optional accelerators; the
portable event archive remains backend-neutral graph truth.

## Restore Safety

Restore is an operator CLI operation and defaults to dry-run validation. Apply
requires a fresh isolated datastore. Exact recovery preserves the source
workspace ID when `--target-workspace` is omitted. Remapped recovery uses typed workspace and namespace transforms;
it never performs generic string replacement in arbitrary JSON. Existing
principal IDs and ACL meaning are retained while workspace scope changes.

The restore path validates checksums, parent continuity, contiguous sequences,
event identity, payload integrity, and target emptiness before importing. Events
are imported idempotently and replayed through the existing engine replay APIs,
which rebuild derived vector rows for the target engine. A fast backend snapshot
is a separate exact-only path: it requires a base archive, a fresh target, and
an exact backend/embedding fingerprint match. Derived projections, Chroma
vectors, indexes, reports, and Obsidian output are rebuildable and are not graph
truth.

Archives contain raw source and application artifacts when present in the
configured data directory. Credentials, tokens, provider keys, and host
secrets are excluded. Encryption is provided by the operator's filesystem,
backup, transport, or KMS tooling.

## Operations

```text
python -m kogwistar_llm_wiki archive create --workspace W --output W-base.tar.gz
python -m kogwistar_llm_wiki archive create --workspace W --parent W-base.tar.gz --output W-delta.tar.gz
python -m kogwistar_llm_wiki archive verify --archive W-base.tar.gz
python -m kogwistar_llm_wiki archive restore --archive W-base.tar.gz
python -m kogwistar_llm_wiki archive restore --archive W-delta.tar.gz --parent W-base.tar.gz --target-workspace W-copy --apply
```

For a fast exact Chroma recovery, create the archive with
`--include-backend-snapshot`, then pass `--use-backend-snapshot` and the exact
`embedding_fingerprint` from `archive inspect`. This path copies the complete
recorded persistence directories, not one collection directory.

Capture requires LLM-Wiki writers to be stopped or drained. Known pending or
doing durable index jobs cause capture to fail closed. External processes that
bypass LLM-Wiki coordination remain the operator's responsibility.

## Consequences

Portable archives work across Chroma, SQLite, and PostgreSQL-backed LLM-Wiki
deployments, and permit deterministic replay from a known watermark. They are
larger and slower to restore than a matching backend snapshot. Chroma restore
requires vector rebuilding when the workspace or embedding fingerprint
changes. Archive files may contain sensitive source text, so access control and
external encryption are mandatory operational concerns.
