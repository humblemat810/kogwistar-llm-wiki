# llm-wiki pi-agent extension

Install the standalone TypeScript extension into pi's global or project
extension directory:

```powershell
Copy-Item .\integrations\pi-agent\llm-wiki.ts $HOME\.pi\agent\extensions\
$env:LLM_WIKI_BASE_URL = "http://127.0.0.1:8765"
$env:LLM_WIKI_WORKSPACE = "demo"
pi
```

It can use the semantic MCP surface: `query`, `search`, `ingest`, `source`,
`reingest`, `maintain`, `status`, `hypergraph_search`, `history`, `propose`,
and `confirm`. Confirmation is explicit and must not be called without human
or policy approval. Source capture still uses the canonical ingestion path and
the server's configured provenance policy.

For a trusted local personal server, set `LLM_WIKI_AUTH_MODE=disabled` and
leave `LLM_WIKI_API_TOKEN` unset. The extension still sends its configured
`LLM_WIKI_WORKSPACE`; disabled mode supplies no identity and does not require
an ACL. Use static-token or JWT mode before exposing the gateway to others.

This extension provides Pi convenience tools for asking, searching, proposing,
and confirming. The native MCP server remains the complete eleven-tool
interface, including source lifecycle, maintenance, status, hypergraph search,
and history capabilities.
