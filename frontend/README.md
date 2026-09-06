# Knowledge Workbench Frontend

This is an additional application projection, not an Obsidian replacement.

```powershell
cd frontend
npm install
npm run dev
```

For browser regression tests, install the Playwright browser once and run
`npm run test:e2e`.

The client requests `GET /api/lens?query=...` and falls back to the checked-in
offline fixture when the API is unavailable. The API response must match the
`SemanticLensSnapshot` JSON shape emitted by `SemanticLensSnapshot.to_dict()`.
The browser only displays a bounded snapshot. Any edit action must be wired to
the llm-wiki validation/confirmation endpoint before it can mutate the graph.

For a real local graph, run the app-owned transport on port `8765`, construct
`WorkbenchApi` with a configured `IngestPipeline`, and call
`serve_workbench(api)`. Vite proxies `/api/*` to that transport, so the browser
uses the real lens while keeping a same-origin URL. The transport serves
lens/history reads plus `POST /api/ask`. The ask endpoint persists a grounded
investigation turn and can invoke a host-injected Codex listener when the
browser selects Codex mode. Graph edits remain explicit, validated commands
through the application layer. If the transport is unavailable, the client
falls back to the checked-in fixture and visibly remains bounded/offline.
`POST /api/proposal/validate` validates a proposal against the current lens;
it does not mutate the graph or bypass confirmation.
