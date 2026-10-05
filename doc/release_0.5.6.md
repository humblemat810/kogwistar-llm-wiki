# LLM-Wiki 0.5.6

## Highlights

- Adds request-local, deterministic prompt aliases for typed cockpit and
  background cross-link provider calls.
- Restores canonical node and edge IDs before validation, ACL checks, source
  evidence checks, and persistence; aliases are never stored in graph or
  conversation artifacts.
- Keeps ACL and source-evidence fields out of automatic model context while
  retaining canonical authorization and audit state internally.
- Aligns the release with Kogwistar `0.6.3`, which provides typed and
  concurrency-safe alias resolution for nodes and edges.

## Compatibility

- Existing canonical graph IDs, parser transport IDs, source pointers, ACL
  scopes, namespaces, revisions, and persisted history remain unchanged.
- KG Doc Parser does not adopt prompt aliases because its document-local
  transport IDs are part of parse reconstruction rather than model-visible
  graph context.

## Release Gate

Publish only after the Kogwistar `0.6.3` commit is merged and the full LLM-Wiki
CI matrix passes on CPython 3.12-3.14 and PyPy 3.11.
