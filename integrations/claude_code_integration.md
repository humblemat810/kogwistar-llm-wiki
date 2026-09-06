# Claude Code Integration

The llm-wiki MCP server is intended to run as a separate service from Claude
Code. Start the container with `LLM_WIKI_AGENT_API_ENABLED=true` if REST agent
routes are also needed, and configure a token for remote MCP:

```powershell
$env:LLM_WIKI_MCP_AUTH_REQUIRED = "true"
$env:LLM_WIKI_MCP_TOKEN = "replace-with-a-secret"
docker compose up -d
claude mcp add --transport http llm-wiki http://127.0.0.1:8780/mcp `
  --header "Authorization: Bearer $env:LLM_WIKI_MCP_TOKEN"
```

The exact `claude mcp` flags can vary by Claude Code version. The equivalent
project `.mcp.json` shape is:

```json
{
  "mcpServers": {
    "llm-wiki": {
      "type": "http",
      "url": "http://127.0.0.1:8780/mcp",
      "headers": {
        "Authorization": "Bearer ${LLM_WIKI_MCP_TOKEN}"
      }
    }
  }
}
```

For a trusted local single-user server, authentication can be disabled
explicitly with `LLM_WIKI_AUTH_MODE=disabled`; omit the Authorization header in
that case. This is no-identity mode, not a default account, and every tool
still needs the intended `workspace_id`. Use static-token or JWT mode before
sharing the server or binding it beyond localhost.

Use the `query` and `search` tools for grounded reads. Treat
`propose` as a review operation and call `confirm` only after
the user explicitly approves the proposal. A proposal may validly contain no
changes.

Use `ingest`, `source`, and `reingest` for canonical source lifecycle; use
`maintain` for directed asynchronous knowledge management and `status` for
health/state. Use `hypergraph_search` only for bounded structural inspection
and `history` for prior interaction context. The server rejects unallowlisted
URI fetches and enforces its configured provenance policy.
