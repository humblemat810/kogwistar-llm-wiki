# Repository Structure

LLM-Wiki is the product and orchestration repository. The sibling directories
`kogwistar/`, `kg-doc-parser/`, and `kogwistar-obsidian-sink/` are vendored
checkouts with their own ownership, CI, and release lifecycle. Root changes
must not silently modify those repositories.

## Ownership

| Area | Root location | Responsibility |
| --- | --- | --- |
| Product runtime | `src/kogwistar_llm_wiki/` | LLM-Wiki orchestration, API, maintenance, Codex, embeddings, and projections |
| Application contracts | `src/kogwistar_llm_wiki/app_contracts/` | Typed messages and DTOs owned by LLM-Wiki |
| Maintenance domain | `src/kogwistar_llm_wiki/maintenance/` | Maintenance implementations, policy, selection, patches, and reporting |
| Parsing facade | `src/kogwistar_llm_wiki/parsing/` | Revision-pinned parse sessions, generation evidence, ParseViews, and reconciliation |
| Embedding facade | `src/kogwistar_llm_wiki/embeddings/` | Product embedding adapters, multimodal projections, and source-unit handling |
| Codex facade | `src/kogwistar_llm_wiki/codex/` | Codex bridge, structured runner, and project-memory APIs |
| Embedding service | `src/llm_wiki_embedding_service/` | Isolated model-serving process |
| Application tests | `tests/` | Root product behavior and integration contracts |
| Product documentation | `doc/` | ADRs, operator procedures, architecture, and testing guidance |
| Operational tooling | `scripts/` | Release, Docker, Codex, model, and benchmark commands |
| Frontend | `frontend/` | Workbench UI and browser tests |

## Dependency Boundary

Reuse Kogwistar primitives for graph reads and writes, leases, named
projections, ACLs, provenance, and backend capabilities. Reuse parser contracts
for source-unit and layered parsing semantics. LLM-Wiki adds product policy,
workspace orchestration, maintenance selection, and external service adapters.

Do not copy a generic queue, transaction, graph, or grounding primitive into
the root application. If a capability is broadly reusable, propose it in the
owning dependency repository and update the pinned revision after its CI passes.

## Source Navigation

The source package is being migrated incrementally toward bounded contexts:

```text
maintenance/        maintenance implementations and public domain facade
parsing/             public parsing facade over parse_*.py modules
embeddings/          public embedding facade over multimodal_*.py modules
codex/               public Codex facade over codex_*.py modules
parse_*              durable parse sessions, generations, views, and comparison
multimodal_*         assets, grounding, projection, and runtime adapters
codex_*              Codex bridge, memory, and workbench integration
workbench_*          REST/workbench APIs and background dispatch
worker* / daemon     durable worker and process orchestration
```

The parsing, embeddings, and Codex facade packages are intentionally thin and
currently preserve their historical implementation locations. The maintenance
package is physically migrated: its implementation files live inside the
package and the old root-level module paths are no longer part of the product
source. This keeps the ownership boundary explicit without duplicating models
or changing persisted/runtime contracts.

The public package facade and current application imports are the supported
entrypoints. A reorganization must preserve Compose service names, health
routes, MCP tool names, persisted projection keys, and CLI entrypoints.

## Test Navigation

Tests are grouped by execution contract rather than implementation file:

```text
tests/unit/          provider-free deterministic behavior
tests/integration/   persistent or external service integration
tests/smoke/         short end-to-end product flows
tests/fixtures/      reusable bounded input payloads
tests/_helpers/      shared markers and test infrastructure
```

New tests should use `ci` for deterministic pull-request coverage, `slow` for
long-running Docker or worker scenarios, and `manual` for real providers,
credentials, GPU workloads, or operator-only actions.
