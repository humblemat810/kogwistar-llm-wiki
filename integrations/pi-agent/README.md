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
