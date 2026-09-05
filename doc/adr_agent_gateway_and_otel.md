# ADR: Agent Gateway Protocols And Optional OpenTelemetry

## Status

Accepted for implementation in the llm-wiki application. Kogwistar core is
not changed by this ADR.

## Context

The interactive graph workbench already provides the grounded lens, answer,
history, proposal validation, and explicit confirmation semantics needed by an
agent. Separate implementations for each protocol would create drift in
grounding, mutation safety, and interaction history.

The application also needs operational visibility across ingestion,
maintenance, workbench interactions, and protocol calls. Existing Kogwistar
trace events and workflow history remain authoritative; telemetry is not a
second accounting or graph store.

## Decision

llm-wiki exposes one app-owned `AgentGateway` over the existing `WorkbenchApi`.
It provides OpenAI-compatible Responses and Chat Completions adapters, a
standards-aligned A2A JSON-RPC binding plus HTTP+JSON compatibility routes, and
an app-level MCP tool bridge for
the following small semantic capability surface:

- Primary reads: `query`, `search`.
- Source lifecycle: `ingest`, `source`, `reingest`.
- Management: `maintain`, `status`.
- Advanced reads: `hypergraph_search`, `history`.
- Controlled mutation: `propose`, `confirm`.

These names are the stable agent-facing contract. CLI commands, queue
administration, worker control, database access, migrations, projection
controls, debug operations, and arbitrary graph writes remain hidden.

`ingest` and `reingest` always use the canonical `IngestPipeline`; MCP never
writes graph storage directly. Source capture accepts raw text plus a stable
URI, or an explicitly allowlisted HTTP(S) URI fetch. Local filesystem paths
are not accepted through the agent boundary. `maintain` queues durable work
through the existing maintenance request, lease, strategy, and worker path and
returns job identifiers rather than exposing queue mechanics.

Ingestion provenance is an explicit request policy: `required` rejects missing
or invalid provenance, `optional` validates supplied provenance but allows
capture without it, and `disabled` removes the requirement without inventing
evidence. Workspace, source identity, revision/span, and excerpt checks remain
authoritative application validation. Text without provenance is retained as
source/user input and is not silently promoted to external evidence.

`maintain` supports request budgets for time, LLM calls, tokens, cost, and
steps. Those values are persisted in the maintenance payload and cumulative
usage is carried across fair-scheduling phase requeues.

All protocol responses include an `llm_wiki` extension containing the grounded
answer payload, lens metadata, citations, and proposal state. Background Codex
interactions use the existing durable workbench interaction store; A2A task IDs
map to those interaction IDs.

The preferred A2A endpoint is `POST /a2a`. It accepts JSON-RPC 2.0 methods
`message/send`, `message/stream`, and `tasks/get`, preserves request IDs, and
returns JSON-RPC error objects. Streaming responses are SSE where each
`data` field contains a JSON-RPC response. `/.well-known/agent.json` declares
protocol version `0.2.6`, the JSON-RPC endpoint, the HTTP+JSON compatibility
interface, supported skills, bearer authentication when configured, and that
push notifications are unsupported. The existing `/a2a/v1/...` routes are
retained for clients using the HTTP+JSON binding.

No protocol writes graph truth directly. A proposal may be validated, and only
an explicit confirmation may apply it through the existing patch service. A
valid result may contain no proposed changes.

Agent routes are disabled unless `LLM_WIKI_AGENT_API_ENABLED=true`. Operators
must put authentication, authorization, rate limiting, and TLS at the service
boundary before enabling them on a non-local bind address.

OpenTelemetry support is optional and app-owned. `LlmWikiTelemetry` is a safe
no-op when disabled or when OTel packages are not installed. It instruments
workbench protocol calls, ingestion trace events, and long-run trace events.
Only primitive correlation fields are emitted by the event adapter; raw source
text, secrets, and full graph payloads are not emitted.

Integration artifacts live outside the Python package: pi uses
`integrations/pi-agent/`, Hermes uses `integrations/hermes-agent/`, and
compatible agents can use `skills/llm-wiki-knowledge/`.

## Invariants

- Every answer is grounded in a bounded lens or explicitly reports insufficiency.
- `hypergraph_search` is read-only and bounded; it cannot mutate graph truth.
- Source lifecycle operations are workspace-scoped and use stable source identity.
- MCP read tools require `read` authorization; source, maintenance, and mutation
  tools require `write` authorization at the HTTP bridge.
- `propose` never applies a change; only explicit `confirm` can cross the
  mutation boundary.
- History and graph events remain the source of truth; OTel is diagnostic.
- Protocol adapters cannot bypass proposal validation or confirmation.
- A2A JSON-RPC responses are correlated to the client request ID and never
  mix `result` and `error` members.
- A2A streams return HTTP 200 with ordered SSE data events containing complete
  JSON-RPC responses.
- Deterministic and Codex cockpit modes remain distinct.
- Disabling OTel cannot change workflow behavior.
- Disabling the agent API cannot change the local workbench contract.
- Obsidian output is unchanged.

## Consequences

There is one place to evolve request normalization and grounding metadata.
Chat Completions is a compatibility surface rather than a second agent
implementation. A2A polling is backed by existing durable interactions rather
than a new task database. The standard OpenAI `usage` field is `null` when the
workbench turn has no token accounting; usage projections remain responsible
for authoritative and estimated cost reporting.

## Rejected alternatives

- Direct graph mutation from a protocol handler, because it bypasses grounding,
  revision checks, and confirmation.
- A second protocol-specific history store, because it duplicates interaction
  semantics.
- Mandatory OTel, because local ingestion and tests must work without an OTel
  collector.
- Kogwistar core changes, because the required seams already exist and these
  adapters are app-owned.
