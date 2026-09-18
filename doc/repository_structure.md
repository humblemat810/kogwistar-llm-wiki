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
| Agent protocol domain | `src/kogwistar_llm_wiki/agent/` | Bounded request parsing, source/provenance safety, protocol serialization, and agent helpers |
| Archive domain | `src/kogwistar_llm_wiki/archiving/` and `archive.py` | Portable archive contracts, manifest validation, event-chain verification, and restore façade |
| Disambiguation domain | `src/kogwistar_llm_wiki/disambiguation/` and `entity_disambiguation.py` | Validated ambiguity artifacts, service operations, reconciliation, and patch policy |
| Maintenance domain | `src/kogwistar_llm_wiki/maintenance/` | Maintenance implementations, policy, selection, worker mechanics, patches, and reporting |
| Parsing domain | `src/kogwistar_llm_wiki/parsing/` | Revision-pinned parse sessions, generation evidence, ParseViews, comparison, reconciliation, and parse-run statistics |
| Ingestion domain | `src/kogwistar_llm_wiki/ingest/` | Graph-space construction, base-KG projection, graph persistence, source lifecycle, parser dispatch, run tracing/statistics, workbench access, and ingestion support wiring |
| Embedding domain | `src/kogwistar_llm_wiki/embeddings/` | Product embedding adapters, multimodal projections, runtime, grounding, and source-unit handling |
| Codex domain | `src/kogwistar_llm_wiki/codex/` | Codex bridge, structured runner, compose TUI, and project-memory APIs |
| Compose domain | `src/kogwistar_llm_wiki/compose/` and `compose_config.py` | Typed deployment options and fail-closed validation behind the Compose renderer façade |
| Configuration domain | `src/kogwistar_llm_wiki/configuration/` and `settings.py` | Authentication/ACL, desired/effective operator settings, redaction, and runtime health snapshots |
| Provider domain | `src/kogwistar_llm_wiki/providers/` and `provider_config.py` | Shared parser/maintenance provider resolution and compatibility façade |
| Seeding domain | `src/kogwistar_llm_wiki/seeding/` and `graph_seed_bundle.py` | Portable grounded graph-seed schemas and persistence façade |
| CLI domain | `src/kogwistar_llm_wiki/cli/` | Operational command implementations grouped by subsystem |
| Daemon support | `src/kogwistar_llm_wiki/daemons/` | Projection and maintenance lifecycle loops plus shared service-health, startup-recovery, budget, profile-ladder, and background-selection plumbing |
| Workbench domain | `src/kogwistar_llm_wiki/workbench/` | Grounded workbench, graph queries, semantic lens, review, and HTTP/background adapters |
| Embedding service | `src/llm_wiki_embedding_service/` | Isolated model-serving process |
| Application tests | `tests/` | Root product behavior and integration contracts |
| Product documentation | `doc/` | ADRs, operator procedures, architecture, and testing guidance |
| Operational tooling | `scripts/` | Release, Docker, Codex, model, and benchmark commands |
| Frontend | `frontend/` | Workbench UI and browser tests |

Workspace namespace ownership lives in `configuration/workspace.py`, while
provider model discovery lives in `providers/model_catalog.py`. The root
`namespaces.py` and `model_catalog.py` modules remain compatibility facades.

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
agent/               gateway composition, bounded requests, source/provenance, protocol, read/write/maintenance tools, MCP transport, and capability helpers
archiving/           archive contracts, manifest/event validation, safe I/O, and archive operations
disambiguation/      entity-disambiguation schemas, reconciliation rules, service operations, and contracts
maintenance/        maintenance implementations, worker state/budget mechanics, and public domain facade
parsing/             durable parse sessions, generations, views, comparison, quality, parse-run statistics, layered workflow execution, and long-run child/support entrypoints
seeding/             portable graph-seed schemas, grounding validation, and bundle import/export operations
usage/               usage-event persistence, aggregation rules, provider callbacks, projection implementation, and contracts
ingest/              graph-space construction, base-KG projection, graph persistence, source lifecycle, parser dispatch, run tracing/statistics, workbench access, and ingestion support
embeddings/          assets, grounding, projection, runtime, and remote adapters
codex/               Codex bridge, memory, runner, and compose integration
diagnostics/         debug-run helpers, live traces, timing summaries, and parse statistics
compose/             typed Compose options, YAML rendering, validation, and deployment helpers
configuration/       authentication/ACL, desired/effective operator settings, and health snapshots
providers/           shared parser and maintenance provider configuration implementation
policies/             product policy taxonomy, visibility, promotion, lifecycle, and projection rules
projections/          durable projection worker and sink synchronization mechanics
cli/                 argument parsing plus content, archive, embedding, Compose, and server CLI commands
daemons/             projection and maintenance lifecycle loops plus shared health, startup-recovery, budget, profile-ladder, and background-selection support
workbench/           grounded workbench, graph queries, lens, review, and HTTP adapters
workbench_*          REST/workbench APIs and background dispatch
worker* / daemon     durable worker and process orchestration
```

The parsing, ingestion, embeddings, Codex, and maintenance packages contain their
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
