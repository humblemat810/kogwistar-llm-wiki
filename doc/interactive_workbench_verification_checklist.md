# Interactive Workbench Verification Checklist

Use this checklist when changing the semantic lens or frontend. The workbench
is an additional application projection; Kogwistar core and Obsidian remain
unchanged.

## Backend Contract

- [x] Lens reads are scoped by workspace and explicit graph space.
- [x] Lens output is bounded by node, edge, and hyperedge budgets.
- [x] Source grounding and entity revision metadata remain visible.
- [x] Hyperedges remain first-class relation hubs and are not silently turned
  into pairwise facts.
- [x] `no_change` produces no graph mutation.
- [x] Stale watermark, lens, target, and entity-revision proposals are
  rejected before mutation.
- [x] Investigation history is persisted and queryable through the existing
  conversation graph path.
- [x] Deterministic mode and Codex mode share the same bounded lens and
  append-only interaction-history substrate.
- [x] Codex cockpit loop accepts questions, chooses bounded read actions,
  records observations, and ends under a four-action turn budget.
- [x] Codex can emit a typed, grounded node/edge `MaintenancePatch` proposal;
  the host validates its lens/watermark/revision envelope before confirmation.
- [x] Approved Codex proposals use the existing append-only command path and
  refresh the authoritative lens; rejected/no-change proposals do not mutate.
- [x] Proposal scope, evidence documents, durable interaction identity, and
  confirmation history are enforced by the host.
- [x] Cockpit patches are workspace-scoped, capped at twelve operations, and
  can only cite visible lens entities; conversation/thread maintenance checks
  existing target metadata against its declared logical scope.
- [x] A durable confirmation receipt makes repeated confirmation idempotent at
  both graph and audit-history levels.
- [x] Application-time revision checks reject targets without a safe revision
  or targets changed since proposal creation.
- [ ] First-class hyperedge proposal and multi-turn continuation after an
  applied patch need a dedicated command schema and test corpus.
- [x] Synchronous injected listeners remain supported through `/api/ask`.
- [x] Codex browser turns use durable `/api/interactions` submit/status
  endpoints rather than holding an HTTP request open for the model call.
- [x] Request and first terminal result artifacts are append-only and survive
  a persistent-engine restart.
- [x] Claim tokens reject stale completion; late results never overwrite the
  accepted result.
- [x] Active Codex output renews the lease, while a silent/stuck process stops
  receiving renewals.
- [x] Multiple workers claim distinct interactions without duplicate model
  execution, and dispatcher shutdown does not deadlock on instant jobs.
- [x] The installed Codex CLI runs ephemeral and read-only, receives only the
  bounded lens, and cannot mutate graph state directly.
- [x] The transport adapter exposes lens, history, and proposal-validation
  endpoints without creating a second storage model.
- [x] Proposal validation re-resolves the same anchor, pin, budget, and
  tombstone context used to create the lens.

## Browser Interaction

- [x] Initial lens renders with graph, evidence drawer, grounding, watermark,
  and accessible claim list.
- [x] The header identifies whether the lens came from the live transport or
  the offline fixture fallback.
- [x] Clicking a graph/list node requests an anchored bounded expansion.
- [x] Pinned node IDs are sent with subsequent lens requests.
- [x] Follow-up support/tension actions submit a new query rather than only
  changing the input field.
- [x] Query responses show the grounded answer, outcome, citation count, and
  Codex-listener availability when that mode is selected.
- [x] A newer browser question cancels stale polling so an older model response
  cannot replace the current answer.
- [x] Proposal validation status is visible and confirmation-gated.
- [x] Deterministic and Codex mode controls are selectable.
- [x] Hyperedges render as labeled hubs, not false cliques.
- [x] Desktop layout has no clipping in the graph or evidence drawer.
- [x] Mobile layout stacks graph, evidence, and accessible claims without
  horizontal overflow of the graph labels.
- [x] Reduced-motion CSS behavior is present.
- [x] Browser smoke captures desktop and mobile screenshots and reports no
  console/page errors.
- [x] Vite development proxies `/api/*` to the app-owned workbench transport,
  allowing real graph reads instead of silently relying on the fixture.
- [x] Live Codex failures remain visible; only deterministic mode may fall
  back to the offline fixture.
- [x] Codex-mode anchored lens failures remain visible and preserve the last
  authoritative lens instead of replacing it with the offline fixture.
- [x] The browser carries `?workspace_id=<workspace>` into graph and
  interaction requests.

## Commands

```powershell
# Python application contract tests
.\.venv\Scripts\python.exe -m pytest tests/test_import_path_smoke.py tests/test_models.py tests/unit/test_graph_space_query.py tests/unit/test_semantic_lens.py tests/unit/test_investigation_history.py tests/unit/test_workbench.py tests/unit/test_workbench_background.py tests/unit/test_codex_workbench_agent.py tests/unit/test_workbench_api.py tests/unit/test_workbench_http.py -q -p no:cacheprovider

# Frontend type and browser tests
cd frontend
npm run typecheck
npm run build
npx playwright test
```

The browser suite covers initial rendering, pin/mode controls, responsiveness,
anchored expansion, proposal validation, async Codex polling, and stale-answer
cancellation. A manual real-agent smoke also starts the workbench transport,
uses the installed Codex CLI, and drives the browser without route mocks. For
manual review, inspect both a desktop viewport and a 390px-wide mobile
viewport after changes to graph layout or evidence panels.
- [x] Grounded seed bundles validate unique exact excerpts and half-open spans.
- [x] Seed import is idempotent and rejects cross-bundle entity-ID collisions.
- [x] Ordinary edges and multi-endpoint hyperedges survive persisted export and reseed.
- [x] Source text and primary-source URLs survive export without the input fixture.
- [x] A fake cockpit end-to-end test reads the seeded graph before export/reseed.
- [x] A real Codex cockpit smoke can run from `seed-bundle --cockpit-question`.
- [x] Codex structured output uses a strict transport envelope and host-validates patch JSON.
