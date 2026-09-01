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

The app-level MCP bridge exposes:

- `llm_wiki.ask`: grounded question and answer.
- `llm_wiki.search`: bounded graph lens.
- `llm_wiki.history`: investigation history.
- `llm_wiki.propose`: validate an edit without applying it.
- `llm_wiki.confirm`: apply an already validated proposal explicitly.

The `confirm` tool is a mutation boundary. Never call it merely because an
LLM suggested an edit. First validate the proposal, show or review the result,
then send an explicit confirmation using the same interaction and proposal.
A valid answer may propose no changes.

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
