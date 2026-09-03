# Agent Gateway Quickstart

This document is the shortest operational context for a person, AI coding
agent, or other agent client integrating with llm-wiki.

## Start The Server

The existing `workbench` command serves the local graph workbench and the
agent protocol routes. Agent routes are deliberately disabled by default.

PowerShell with Chroma:

```powershell
$env:LLM_WIKI_AGENT_API_ENABLED = "true"
llm-wiki --data-dir .\data --backend chroma workbench --workspace demo --host 127.0.0.1 --port 8765
```

PowerShell with PostgreSQL:

```powershell
$env:LLM_WIKI_AGENT_API_ENABLED = "true"
llm-wiki --data-dir .\data --backend postgres `
  --dsn "postgresql://user:pass@localhost:5432/dbname" `
  workbench --workspace demo --host 127.0.0.1 --port 8765
```

Keep the server bound to localhost for development. For any non-local bind,
put authentication, authorization, rate limiting, and TLS in front of it.

## Human Operator Check

After starting the server, use the API directly from a browser or PowerShell:

```text
http://127.0.0.1:8765/.well-known/agent-card.json
```

The Python server currently exposes the workbench and agent APIs; it does not
serve static frontend assets at `/`. If a graph-explorer frontend is running
separately, configure it to call this server's `/api/lens`, `/api/ask`,
`/api/history`, and proposal endpoints. Type a question in that frontend,
inspect the bounded subgraph, select nodes to refine the lens, and review the
cited evidence before considering a change. The protocol endpoints below use
the same workbench semantics; they are not a separate knowledge graph.

Quick health checks from PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/.well-known/agent-card.json
Invoke-RestMethod "http://127.0.0.1:8765/api/lens?workspace_id=demo&query=reinforcement%20learning"
```

The first check confirms the A2A capability card is available. The second
check confirms that the server can build a grounded graph lens. An empty lens
is a valid result when the selected workspace has no matching evidence.

When an answer contains a proposal, treat it as a review item, not an applied
change. Validate it, inspect its evidence and current lens, and confirm it
only when the proposed operation is wanted. `no_change` is a successful and
valid outcome.

## Endpoint Map

| Surface | Endpoint | Purpose |
|---|---|---|
| OpenAI Responses | `POST /v1/responses` | Canonical synchronous grounded answer |
| Chat compatibility | `POST /v1/chat/completions` | OpenAI Chat Completions-shaped adapter |
| A2A discovery | `GET /.well-known/agent-card.json` | Agent capability card |
| A2A message | `POST /a2a/v1/message:send` | Synchronous or background task submission |
| A2A stream | `POST /a2a/v1/message:stream` | Task-shaped SSE response |
| A2A task | `GET /a2a/v1/tasks/{id}?workspace_id=...` | Poll durable background interaction |
| MCP bridge | `GET /mcp/tools/list` | List app tools |
| MCP bridge | `POST /mcp/tools/call` | Invoke an app tool |

The MCP bridge applies `read` scope to `query`, `search`, `source`, `status`,
`hypergraph_search`, and `history`. It applies `write` scope to `ingest`,
`reingest`, `maintain`, `propose`, and `confirm`.

The standard OpenAI `usage` field is `null` when the workbench turn has no
provider token usage. Use llm-wiki usage projections for authoritative or
explicitly estimated cost reporting.

## Request Context

Every request should identify:

- `workspace_id`: graph and history scope; required for reliable isolation.
- `session_id`: queryable interaction-history scope.
- `mode`: `deterministic` or `codex`.
- A query or user message.

Responses include an `llm_wiki` extension with the grounded answer payload,
lens metadata, citations, history information, and any proposal state. Agents
should inspect this extension rather than treating the plain answer text as
the complete graph result.

## OpenAI-Shaped Example

```json
{
  "model": "llm-wiki-deterministic",
  "input": "What is connected to reinforcement learning?",
  "metadata": {
    "workspace_id": "demo",
    "session_id": "agent-1"
  }
}
```

Send it to `http://127.0.0.1:8765/v1/responses`.

For Chat Completions, use `messages` instead of `input`:

```json
{
  "model": "llm-wiki-deterministic",
  "workspace_id": "demo",
  "messages": [
    {"role": "user", "content": "What is connected to reinforcement learning?"}
  ]
}
```

## A2A Behavior

If Codex workers are configured, a background A2A request returns a task in
`working` state. Poll the task endpoint until its state is `completed` or
`failed`. Task IDs map to the existing durable workbench interaction IDs; no
second task database is created.

## MCP Tools

The app-level MCP bridge and native MCP server expose exactly these semantic
tools:

| Group | Tools | Meaning |
|---|---|---|
| Primary | `query`, `search` | Grounded answers and bounded retrieval |
| Source | `ingest`, `source`, `reingest` | Canonical source capture and lifecycle |
| Management | `maintain`, `status` | Directed maintenance and health/state |
| Advanced | `hypergraph_search`, `history` | Read-only structure and prior context |
| Controlled mutation | `propose`, `confirm` | Validate, then explicitly apply changes |

`query` returns the grounded answer, citations, lens ID, source watermark, and
an insufficiency/no-change result when appropriate. `search` returns bounded
nodes, edges, hyperedges, grounding, and selection metadata. The advanced
`hypergraph_search` tool is read-only and is not a graph mutation escape hatch.

`ingest` accepts raw text with a stable `source_uri`, or fetches an HTTP(S)
source when its host is explicitly listed in
`LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS`. Local filesystem paths are never
accepted by the agent boundary. `source` can inspect by URI or stable source
ID. `reingest` uses the same canonical pipeline and accepts either identity
plus replacement text or an allowlisted URI fetch.

Ingestion provenance is explicit: `required` rejects missing provenance,
`optional` validates supplied provenance but allows source capture without it,
and `disabled` removes the requirement without fabricating evidence. Supplied
workspace, URI, span, and excerpt fields must agree with the request and raw
text. User-provided text without provenance remains user/source input rather
than silently becoming external authoritative evidence.

`maintain` is asynchronous and returns durable job IDs. Its optional budgets
are `max_time_seconds`, `max_llm_calls`, `max_tokens`, `max_cost_usd`, and
`max_steps`; the worker persists these with the request and carries cumulative
usage across fair-scheduling requeues. `status` reports readiness separately
from an empty knowledge result and includes source and maintenance state.

The `confirm` tool is a mutation boundary. Never call it merely because an
LLM suggested an edit. First validate the proposal, show or review the result,
then send an explicit confirmation using the same interaction and proposal.
A valid answer may propose no changes.

The MCP surface intentionally hides raw queue administration, worker control,
database access, migrations, projection controls, debug operations, arbitrary
graph writes, and internal daemon mechanics.

For a native MCP client such as Hermes, use the full FastMCP server instead of
the lightweight REST bridge:

```powershell
llm-wiki --data-dir .\data --backend chroma mcp --workspace demo --transport stdio
```

For a remote MCP client:

```powershell
llm-wiki --data-dir .\data --backend chroma mcp --workspace demo `
  --transport streamable-http --host 127.0.0.1 --port 8780 --path /mcp
```

The REST bridge is at `/mcp/tools/*`; the native MCP server is the compatible
protocol surface for MCP clients.

For remote native MCP, configure `LLM_WIKI_MCP_AUTH_REQUIRED=true`,
`LLM_WIKI_MCP_TOKEN`, and optionally `LLM_WIKI_MCP_TOKEN_SCOPES`. Static tokens
are suitable for local/private development; use a trusted OAuth-aware provider
or proxy for production identity and authorization.

## Grounding And Ownership

- The graph, workflow history, usage events, and checkpoints remain authoritative.
- OTel spans are diagnostic only and must not be used as graph truth or billing truth.
- Protocol adapters delegate to `WorkbenchApi`; they do not write graph nodes directly.
- Deterministic mode and Codex cockpit mode remain separate execution modes.
- Obsidian output is unchanged by these endpoints.

For design rationale and invariants, see
[`adr_agent_gateway_and_otel.md`](adr_agent_gateway_and_otel.md).

Agent-specific installation files are in
[`integrations/pi-agent/`](../integrations/pi-agent/),
[`integrations/hermes-agent/`](../integrations/hermes-agent/), and the
portable skill is in
[`skills/llm-wiki-knowledge/SKILL.md`](../skills/llm-wiki-knowledge/SKILL.md).
