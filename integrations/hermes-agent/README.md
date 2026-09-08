# llm-wiki Hermes portable plugin

For the complete article ingestion, maintenance, recovery, and agent-serving
runbook, see the [LLM-Wiki Cookbook](../../doc/cookbook.md).

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

For a trusted local personal deployment, set `LLM_WIKI_AUTH_MODE=disabled` and
do not configure a token. This selects the explicit no-identity mode; it does
not create a default user, and the MCP tools still operate within the selected
workspace. Use static-token or JWT mode before sharing the server.

The server exposes the semantic tools `query`, `search`, `ingest`, `source`,
`reingest`, `maintain`, `status`, `hypergraph_search`, `history`, `propose`,
and `confirm`. Use `query`/`search` for grounded reads, `ingest`/`source`/
`reingest` for source lifecycle, `maintain` for directed background work, and
`propose` followed by explicit `confirm` for durable changes. Internal queue,
database, worker, and direct graph-write operations are not exposed.
