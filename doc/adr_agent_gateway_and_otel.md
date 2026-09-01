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
It provides OpenAI-compatible Responses and Chat Completions adapters, A2A-
style agent card/message/task routes, and an app-level MCP tool bridge for
ask, search, history, proposal validation, and explicit confirmation.

All protocol responses include an `llm_wiki` extension containing the grounded
answer payload, lens metadata, citations, and proposal state. Background Codex
interactions use the existing durable workbench interaction store; A2A task IDs
map to those interaction IDs.

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
- History and graph events remain the source of truth; OTel is diagnostic.
- Protocol adapters cannot bypass proposal validation or confirmation.
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
