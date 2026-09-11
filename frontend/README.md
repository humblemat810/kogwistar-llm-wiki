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

The **Settings** button opens the operating console. It reads `/api/settings`
and `/api/settings/health`, showing effective values, staged desired values,
per-space embedding profiles, local Chroma text retrieval, and the optional
Docker Qwen3-VL service. Desired parser/maintenance model changes are stored
under the application data directory and require a graceful restart. Profile
changes additionally require isolated re-embedding. The only live control in
this first console is the multimodal retrieval route toggle; it does not alter
the vector profile. Settings endpoints use the same authentication and
workspace authorization as the rest of the workbench.

Authentication mode is displayed for orientation and can be staged from the
settings console. It is not a live security toggle: changing OAuth/OIDC or
static-token mode requires a graceful restart, Compose/environment updates,
and verification of workspace ACLs before exposure.

The Settings console also includes a **Compose helper**. Select GPU, CPU, or
text-only mode, choose PostgreSQL and an authentication mode, optionally
include Grafana OTel and the OAuth example, and enter the immutable model
revision for Qwen3-VL. The live preview uses the server-side validator. The
standalone static helper at `public/compose.html` implements the same safe
PostgreSQL topology for offline use and rejects embedded Chroma because REST
and MCP are separate processes. Save the preview as a new YAML file, run
`llm-wiki compose check`, then `docker compose config --quiet` before starting
containers. The browser does not start, stop, or rewrite Docker resources.

For configuration without a live LLM-Wiki API, build the frontend and open
`dist/compose.html`, or serve `public/compose.html` from any static web server.
That standalone helper defaults to PostgreSQL and supports CPU/GPU/text-only,
authentication, OTel, and optional OAuth choices. OAuth is an optional
profiled test provider; it does not by itself configure application JWT
verification.
