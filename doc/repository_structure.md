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
| Parsing domain | `src/kogwistar_llm_wiki/parsing/` | Revision-pinned parse sessions, generation evidence, ParseViews, comparison, and reconciliation |
| Embedding domain | `src/kogwistar_llm_wiki/embeddings/` | Product embedding adapters, multimodal projections, runtime, grounding, and source-unit handling |
| Codex domain | `src/kogwistar_llm_wiki/codex/` | Codex bridge, structured runner, compose TUI, and project-memory APIs |
| Workbench domain | `src/kogwistar_llm_wiki/workbench/` | Grounded workbench, graph queries, semantic lens, review, and HTTP/background adapters |
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

The source package is organized around bounded contexts:

```text
maintenance/        maintenance implementations and public domain facade
parsing/             durable parse sessions, generations, views, and comparison
embeddings/          assets, grounding, projection, runtime, and remote adapters
codex/               Codex bridge, memory, runner, and compose integration
workbench/           grounded workbench, graph queries, lens, review, and HTTP adapters
workbench_*          REST/workbench APIs and background dispatch
worker* / daemon     durable worker and process orchestration
```

The parsing, embeddings, Codex, and maintenance packages contain their
implementations. The old root-level implementation paths are no longer part
of the product source. This keeps ownership boundaries explicit without
duplicating models or changing persisted/runtime contracts. The package root
continues to re-export the supported public API; domain-specific code should
import from its owning package.

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
