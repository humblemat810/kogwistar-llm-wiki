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
