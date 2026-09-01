# llm-wiki pi-agent extension

Install the standalone TypeScript extension into pi's global or project
extension directory:

```powershell
Copy-Item .\integrations\pi-agent\llm-wiki.ts $HOME\.pi\agent\extensions\
$env:LLM_WIKI_BASE_URL = "http://127.0.0.1:8765"
$env:LLM_WIKI_WORKSPACE = "demo"
pi
```

It registers grounded ask/search/proposal/confirmation tools. Confirmation is
explicit and must not be called without human or policy approval.
