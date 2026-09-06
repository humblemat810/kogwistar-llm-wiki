# Codex host integration

The Codex executable stays on the host. Run llm-wiki in Docker and connect
Codex to its native MCP endpoint or REST gateway; do not install Codex inside
the llm-wiki image.

For an MCP-capable Codex client, configure this remote server:

```text
http://127.0.0.1:8780/mcp
```

Set the same bearer token configured by `LLM_WIKI_API_TOKEN` in the client
configuration. For a host-driven cockpit, configure the container with
`LLM_WIKI_COCKPIT_CALLBACK_URL` and explicitly allow its hostname through
`LLM_WIKI_COCKPIT_CALLBACK_ALLOWED_HOSTS`. The callback receives a bounded
request, semantic-lens snapshot, and prior observations, and returns a
validated cockpit action. Graph mutations remain behind explicit confirmation.

The semantic MCP tools are `query`, `search`, `ingest`, `source`, `reingest`,
`maintain`, `status`, `hypergraph_search`, `history`, `propose`, and `confirm`.
Use `propose` followed by explicit `confirm`; there is no direct graph-write
tool. Configure `LLM_WIKI_MCP_TOKEN` and `LLM_WIKI_MCP_TOKEN_SCOPES` for the
remote MCP transport.

For a trusted local personal deployment, set `LLM_WIKI_AUTH_MODE=disabled` and
omit the bearer token. The server then has no authenticated principal or ACL
requirement, but the configured workspace remains the data boundary. This mode
is not suitable for a shared host; use static-token or JWT mode when another
user or network can reach the service.
