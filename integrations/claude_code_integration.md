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

Use the `llm_wiki.ask` and `llm_wiki.search` tools for grounded reads. Treat
`llm_wiki.propose` as a review operation and call `llm_wiki.confirm` only after
the user explicitly approves the proposal. A proposal may validly contain no
changes.
