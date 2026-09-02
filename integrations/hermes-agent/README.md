# llm-wiki Hermes portable plugin

This directory follows Hermes Agent Plugins v1 portable layout. Install it
from a repository containing this directory, then enable it:

```text
hermes plugins install <owner>/<repository> --no-enable
hermes plugins enable llm-wiki
```

Set `LLM_WIKI_DATA_DIR`, `LLM_WIKI_BACKEND`, and `LLM_WIKI_WORKSPACE` in the
Hermes environment. The package starts the full MCP server over stdio, so no
HTTP agent flag is needed for this integration. For HTTP MCP, start
`llm-wiki ... mcp --transport streamable-http` and configure Hermes with its
`url` instead; `mcp-http.json` is a starting configuration. Set
`LLM_WIKI_API_TOKEN` when the remote server requires bearer authentication.
Keep shared deployments behind authentication.
