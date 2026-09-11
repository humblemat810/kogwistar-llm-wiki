# ADR: Interactive Knowledge Workbench Over Existing Kogwistar Contracts

- Status: Proposed
- Scope: `kogwistar-llm-wiki` product UI, interaction, and projection adapter
- Related: `architecture.md`, `kogwistar_rust_port_compatibility_reminder.md`,
  `adr_usage_attribution_projections.md`

## Context

Obsidian is a useful durable document projection, but it is not an effective
interactive surface for exploring a living, provenance-rich Kogwistar graph.
The earlier D3 debug dump is also unsuitable as a product surface: it attempts
to show too much at once, resets layout abruptly, and has no model of a user's
limited working memory.

The intended experience is different from opening a whole graph:

1. A person types an idea or selects a saved question.
2. The system retrieves a small, explainable set of relevant graph anchors.
3. It expands a bounded, useful local subgraph around those anchors.
4. Clicking, pinning, changing time, or changing a relationship filter changes
   the visible subgraph by a small animated delta, rather than replacing it.
5. The person can inspect every displayed claim, relationship, provenance,
   confidence, and revision state without treating the UI as graph truth.

This must remain compatible with the Rust Kogwistar migration. Existing
Kogwistar contracts are an external dependency for this implementation. This
ADR does not authorize changes to the Kogwistar core, Rust crates, core Python
facade, core schemas, or core storage behavior. The workbench must use the
existing facade and contract version as-is; any future core enhancement is a
separate ADR and release.

The explorer must not introduce a second graph store, alternate identity
model, or UI-owned mutation protocol.

The reference project at `humblemat810/llm-wiki` is valuable for its local
workbench, health, review, and learning-loop ideas. It is not a storage or
architecture template for this system: its browser-local graph store must not
be copied into a Kogwistar consumer.

## Decision

Build a dedicated, local-first web workbench with a **read-side semantic-lens
projection** and an explicit **write-side command proposal path** over a scoped
Kogwistar graph snapshot. Viewing is only the read-only subset of the product's
knowledge-integration loop. The workbench complements Obsidian; it does not
replace the authoritative graph or make Obsidian obsolete.

The first implementation should be a TypeScript/React application using:

- `Sigma.js` and `graphology` for GPU-backed graph rendering and graph state;
- a Web Worker for layout, scoring, local filtering, and display-delta
  preparation;
- a small custom WebGL/HTML overlay layer for hyperedges, labels, selection,
  provenance badges, and accessible details;
- a versioned JSON API or local development adapter supplied by llm-wiki.

Sigma.js is selected because it is WebGL-oriented and designed for graph
exploration at thousands of visible nodes and edges. It is not selected as a
graph database or semantic query engine. React owns controls and panels, not
the per-frame graph renderer. A future renderer replacement is acceptable if
it preserves the projection payload and interaction contract.

### Current Implementation Baseline

The first application slice is implemented without a Kogwistar source change:

- `src/kogwistar_llm_wiki/semantic_lens.py` provides the bounded,
  deterministic lens projection over existing scoped node/edge reads.
- `src/kogwistar_llm_wiki/investigation_history.py` stores meaningful turns as
  app-owned conversation artifacts through the existing conversation graph
  path; it does not create a second history store.
- `src/kogwistar_llm_wiki/workbench_api.py` is the transport-neutral adapter;
  `workbench_http.py` supplies the app-owned HTTP transport used by local
  development, and Vite proxies `/api/*` to it.
- `src/kogwistar_llm_wiki/workbench.py` provides the shared deterministic and
  Codex-mode grounded answer, proposal-validation, and confirmation path.
- `POST /api/ask` runs a deterministic or injected-listener turn synchronously.
  Codex mode uses `POST /api/interactions` plus `GET /api/interactions`: the
  request and first terminal result are append-only conversation artifacts,
  while the existing durable Kogwistar job facade supplies claim tokens,
  leases, retries, and restart recovery.
- `src/kogwistar_llm_wiki/codex_workbench_agent.py` runs the installed Codex
  CLI as an ephemeral, read-only central reasoning worker. JSONL activity
  renews the 150-second lease only while the process is making progress. A
  late worker that lost ownership cannot overwrite the accepted result. The
  same bounded responder can optionally use `codex app-server --stdio`: it
  performs the App Server JSON-RPC handshake and one ephemeral read-only turn,
  streams assistant deltas, supports cockpit output schemas, and always closes
  the child process on timeout or completion. The default remains `codex exec`
  for backwards compatibility; select App Server transport with
  `--codex-transport app_server` or `KOGWISTAR_CODEX_TRANSPORT=app_server`.
- `python -m kogwistar_llm_wiki ... workbench` starts the local HTTP transport,
  recovers pending interactions, and spawns a configurable bounded worker
  pool. The agent receives only the bounded lens and has no direct graph-write
  access.
- `frontend/` contains the Sigma/Graphology workbench MVP and an offline
  fixture fallback for local development.
- `tests/fixtures/rl_verifiable_reasoning_v1.json` and the application unit
  tests provide a deterministic grounding and no-change contract fixture.

The browser package is intentionally not an HTTP server. The included
`workbench` command is the local/default host; another host may expose the same
`WorkbenchApi`. Transport remains separate from graph semantics.

### Codex Mode: Target Contract And Implemented Bounded Slice

**Target contract.** Codex mode is the cockpit steward for an interactive
session. It listens to meaningful user actions such as a question, selected
node, pin, evidence inspection, or explicit edit request. From the current
session/history and bounded graph context it may decide to answer, ask a
clarifying question, retrieve another bounded lens, inspect evidence/history,
or propose a grounded graph patch. A patch may add a node, edge, or hyperedge,
add evidence, request review, or be `no_change`.

Codex does not write authoritative graph state itself. It emits typed actions
and patch proposals. The app resolves tool calls, records observations, checks
grounding/scope/revision/policy, obtains required confirmation, and only then
uses the existing Kogwistar command path to append events or tombstones. The
agent may continue from the resulting authoritative observation until the turn
reaches an answer, a pending review, `no_change`, or a configured budget.

**Implemented bounded slice.** The durable background worker runs a maximum of
four typed actions per interaction. It can answer, return `no_change`, request
clarification, resolve a smaller bounded lens, inspect visible evidence, query
session history, or emit a typed `MaintenancePatch` proposal. Tool observations
and action rationale are retained in the terminal interaction result. The
current patch surface supports grounded node and ordinary-edge proposals; a
first-class hyperedge proposal remains future work.

The proposal envelope carries the exact lens ID, source watermark, target IDs,
visible evidence IDs, and expected revisions. Each graph-changing operation's
provenance must agree with the source documents of those visible evidence
items, and the patch scope must equal the lens workspace. The current cockpit
accepts workspace-scoped patches only: conversation and thread patches remain
available to their explicit maintenance flows, where existing target entities
are checked against the requested logical scope. The browser shows an explicit
confirm/dismiss decision tied to the durable interaction ID.
Confirmation loads the stored proposal and stored proposal request, re-resolves
that request, validates the envelope and existing maintenance-patch contract,
then delegates append-only application to
`apply_maintenance_patch_for_scope` with revision checks. A confirmation is
also recorded in investigation history. A durable confirmation receipt makes a
repeat confirmation return the original result without creating another audit
record or reapplying the patch. Cockpit patches are capped at twelve operations
per turn. Generic proposal validation also rejects evidence outside its scoped
lens, so a readiness indication cannot rely on fabricated evidence IDs.
No confirmation, an invalid proposal, a stale lens, a missing safe revision,
or `no_change` leaves the graph unchanged. Codex remains read-only and cannot
call this mutation path. Live Codex and live lens transport failures are
surfaced as errors in Codex mode; the offline fixture is only a
deterministic-mode development fallback.

## Ownership And Boundaries

| Concern | Owner |
| --- | --- |
| Node, edge, hyperedge, provenance, tombstone, event ordering, stable identity, workspace isolation | Existing Kogwistar core contract, consumed through its compatible facade |
| Graph reads, vector queries, event append/replay, named projections, graph mutations, snapshot/version metadata | Existing Kogwistar APIs; no core changes in this ADR |
| Workspace/graph-space policy, presentation eligibility, semantic-lens defaults, review controls, saved views | llm-wiki |
| Browser cache, viewport state, pins, layout coordinates, animation, keyboard controls | Explorer client |
| Markdown vault files and deterministic note rendering | Obsidian sink |

The explorer receives immutable snapshot data and may cache it locally. Its
cache is disposable. A UI edit, review action, or maintenance request must be
submitted as an explicit app command and be acknowledged by an authoritative
event before the client treats it as committed.

The shared interaction loop is:

```text
query -> retrieve -> inspect -> reason -> propose edit -> validate -> commit -> refresh
```

The workbench has two orchestration modes over the same lens and command
contracts:

- **Codex-agent mode** is the bounded cockpit steward: it decides the next
  typed read action and may produce a grounded node/edge patch proposal, but
  has no direct storage access or implicit confirmation right.
- **Deterministic workflow mode** uses fixed transitions selected by the host
  for retrieval, question/answer interaction, proposal creation, revision
  validation, and commit. It does not let a model choose the next tool action.

Both modes must produce cited answers and an explicit outcome. The outcome may
be a typed mutation proposal, a review request, or `no_change` when the evidence
does not justify changing the graph. A proposal is not a mutation. A stale
proposal is rejected or returned to review when its source watermark or
expected graph revision no longer matches.

## Grounding, Lineage, And Scope Invariant

The implementation must preserve one invariant across source parsing,
conversation, workflow, knowledge, wisdom, and projections:

> Every persisted semantic claim, derived artifact, answer, workflow outcome,
> and displayed graph item must have explicit, scope-safe lineage.

"Everything is grounded" does not mean that every object must contain a
document character span. The required evidence depends on the object kind:

| Object | Required grounding or lineage |
| --- | --- |
| Source-derived node or edge | Source document ID, source revision, exact span/excerpt, and parser offset semantics |
| Derived knowledge | Grounded source span or a grounded upstream artifact, plus derivation references |
| Conversation message | Workspace, conversation, turn, actor, and timestamp; factual assistant content also cites graph/source evidence |
| Workflow run/step | Run, step, input/output artifact IDs, tool/config version, and emitted graph events |
| `execution_wisdom` | Referenced workflow history and the grouped execution evidence that produced the pattern |
| Factual or generalized wisdom | Execution lineage plus source or grounded knowledge evidence when it makes a domain claim |
| Semantic lens item | Authoritative entity ID, graph space, entity revision, source watermark, projection/lens ID, and selection reason |
| UI answer or no-change outcome | Investigation/session ID, lens ID, source watermark, cited evidence or explicit insufficiency reason |

The canonical chain is:

```text
source document -> source span -> assertion/edge -> derived artifact
    -> semantic lens -> answer or projection
```

Operational events use execution lineage rather than fabricated source spans.
User-provided statements are explicitly user-asserted evidence and are not
automatically promoted to accepted knowledge. An ungrounded factual proposal
must become `review_required` or `rejected`.

The implementation must reuse existing Kogwistar `Grounding`, `Span`, source
reference, event, namespace, revision, and projection metadata contracts. It
must not define competing app-local versions of those concepts. Where the
existing contract does not expose a desired field, llm-wiki stores a reference
to the available authoritative artifact rather than changing core.

## Existing-Core Usage Plan

The workbench composes existing capabilities only:

### Portable grounded seed bundles

Reusable learning datasets are app-owned curated-knowledge inputs, not new
Kogwistar primitives. A versioned seed bundle maps onto existing Kogwistar
`Node`, multi-endpoint `Edge`, `Grounding`, and `Span` models in the workspace's
curated graph namespace. Each paper or report is persisted as a source entity;
concepts, ordinary relations, and hyperedges carry exact half-open source
spans into the bundle's curated paraphrase. This keeps every displayed claim
grounded while avoiding a false claim that the paraphrase is verbatim paper
text.

The app validates global ID uniqueness, endpoint existence, ordinary-edge and
hyperedge cardinality, source membership, and exact or uniquely resolvable
excerpts before writing. Bundle ownership is persisted with entities, and an
ID already owned by another bundle is an error. Re-seeding the same bundle is
idempotent. Export reconstructs JSON from persisted entities and source text;
it must not merely echo the input file. A canonical export can seed a different
workspace and must reproduce the same source, node, edge, hyperedge, and
grounding content.

Cockpit review is an end-to-end acceptance path for these datasets. The
cockpit reads the persisted graph through a normal semantic lens and records
its interaction history. It may answer, return `no_change`, request bounded
inspection, or propose a separately confirmed patch. Successful cockpit review
does not waive deterministic round-trip integrity checks.

- existing scoped graph reads for nodes, edges, and supported hypergraph data;
- existing vector query APIs after llm-wiki supplies an embedding;
- existing namespace and graph-space filtering;
- existing event append/replay and conversation/workflow history;
- existing graph mutation and tombstone commands;
- existing named projections and sequence watermarks;
- existing provenance and source-pointer validation.

llm-wiki adds the `SemanticLensService`, investigation orchestration,
selection/scoring policy, answer policy, command-confirmation policy, and
projection adapter around these APIs. It does not add a Rust `SemanticLens`
framework or modify Kogwistar storage to support this ADR.

The interaction history is queryable. Each meaningful interaction records a
durable, bounded history event containing the workspace, session/conversation
ID, lens ID, source watermark, question or action kind, selected entities,
answer outcome, proposal outcome, and correlation IDs. Raw keystrokes, pointer
movement, and transient animation state remain client telemetry unless the user
explicitly saves them. History is evidence about the investigation, not a
replacement for graph truth.

## Semantic Lens Contract

A semantic lens is a versioned, bounded answer to a graph-exploration request.
It is not a claim that the returned subgraph is complete.

### Request

```text
workspace_id
graph_spaces
query_text | explicit_anchor_ids
semantic_retrieval: enabled | disabled
hop_limit: 0..N
relation/type/status filters
time_window and revision/tombstone policy
display_budget: nodes, edges, hyperedges, labels
pinned_node_ids
previous_lens_id (optional)
```

`graph_spaces` is explicit. The default learning view reads promoted
`curated_kg` and, when enabled, evidence references in `source`; it does not
silently mix review, maintenance, or wisdom artifacts into factual knowledge.

### Resolution

The server or projection worker resolves a lens in ordered stages:

1. Validate workspace, graph-space, viewer permissions, and a fixed source
   sequence watermark.
2. Retrieve candidate anchors with hybrid lexical/embedding retrieval. Each
   anchor records why it matched: query term, embedding score, explicit pin, or
   traversal from another retained entity.
3. Traverse permitted relation types within the hop budget. Traversal uses
   canonical identities and respects tombstone and graph-space policy.
4. Score candidates using relevance, evidence quality, relation confidence,
   recency when requested, structural bridge value, and a diversity penalty.
5. Preserve pins and the currently focused node; choose the remaining entities
   under the display budget.
6. Emit a deterministic display graph and a delta from the previous lens when
   possible.

The score is a display-selection heuristic, not knowledge confidence and not a
truth score. Every selected entity carries its own semantic confidence and
provenance independently.

### Response

```text
lens_id, workspace_id, source_watermark, projected_at_ms
completeness: bounded | exhausted | partial_due_to_budget
nodes, edges, hyperedges
participations                 # an explicit hyperedge-to-member relation
anchor_explanations
selection_explanations
omitted_summary                # counts/reasons, never hidden as absence
delta: add | update | remove | retain
query_timing and cache metadata
```

The fixed `source_watermark` is the correctness boundary. `projected_at_ms`
only says when the lens was materialized. A newer event may make a displayed
lens stale, but does not make its historic snapshot dishonest.

## Interaction Model

The primary view has three coordinated surfaces:

- **Idea bar:** natural-language query, graph-space scope, hop selector, time
  range, and a small set of saved lenses such as "current evidence", "recent
  changes", and "contradictions".
- **Graph canvas:** pan/zoom, smooth bounded expansion, focus halo, visual
  grouping by type, and an always-visible result count/budget indicator.
- **Evidence drawer:** details for the focused entity: assertion text,
  relationship semantics, confidence, source spans, event/revision lineage,
  and actions to pin, compare, inspect source, or request review.

The initial lens must be modest: target 30--80 nodes, 60--160 binary edges,
and at most 12 visible hyperedges. The user can intentionally expand it, but
the client must never surprise them by rendering an entire workspace.

Clicking a node retains the clicked node, adds a focused neighborhood, and
evicts the lowest-value unpinned entities. Pinned entities survive all normal
eviction. A breadcrumb records the lens path so Back restores the exact prior
snapshot and layout.

The workbench may change the graph as an investigation proceeds, but only
through the shared command path. Typical commands include adding grounded
evidence, adding a relation, creating a review request, proposing a merge,
tombstoning an incorrect fact, and creating a disambiguation candidate. Each
command carries target identities, evidence references, the expected graph
revision, and the policy/confirmation result.

Changing filters computes a delta rather than clearing the canvas. Existing
nodes retain positions where practical; incoming nodes begin near their
strongest retained neighbor; departed nodes fade and shrink. Layout changes are
animated over a short, interruptible interval. The renderer must not run a
global force simulation continuously, because preserving the user's mental map
matters more than a globally optimal layout.

Useful user-facing safeguards include:

- keyboard navigation and non-canvas list/evidence alternatives;
- a visible legend and "why is this here?" explanation for every entity;
- a one-click reset to the initial lens;
- explicit distinction between accepted knowledge, candidate/review artifacts,
  and tombstoned historical entities;
- shareable, redacted lens links containing snapshot/filter state but not
  secrets or unapproved source text;
- a reduced-motion mode and an accessible table view of the same lens.

## Hyperedge Semantics

Do not silently project a hyperedge into a clique. A clique falsely suggests
pairwise facts and hides the relation's own identity, provenance, confidence,
and lifecycle.

The transport payload represents a hyperedge as a first-class entity plus
explicit participation records. The canvas renders it as a labeled relation
hub, bracket, or compact relation card connected to its participants. Dense
hyperedges collapse to a single summary glyph until focused. Clicking the glyph
opens its members and evidence. Edge-to-edge relationships are rendered with
the same explicit identity rule.

## Performance And Smoothness Policy

The target is not maximum raw graph size; it is a stable 60 fps interaction
with a bounded visible graph.

- Retrieve and score server-side or in a projection worker; do not ship an
  entire workspace to the browser for every query.
- Render only the current lens. Maintain a small client cache of recent lenses
  keyed by snapshot watermark and normalized request.
- Perform layout/scoring work in a Web Worker. The main thread only performs
  interaction, renderer updates, and small UI state changes.
- Batch graph mutations per animation frame and use stable entity IDs as
  renderer keys.
- Start with a performance gate for 1,000 visible nodes / 3,000 visible binary
  edges on a reference developer laptop, while normal lenses remain far below
  that budget.
- Measure query-to-first-render, delta-apply time, frame time, dropped frames,
  selection latency, cache hit rate, and detail-panel latency. Do not claim
  smoothness without these measurements.

The explorer should subscribe to a bounded change feed after its snapshot.
Incoming changes produce a "new graph changes available" indicator. Automatic
visual mutation is allowed only for a small focused set and must be reversible;
otherwise the user chooses Refresh, preserving reading context.

## Learning Fixture: Reinforcement Learning For Verifiable Reasoning

Seed a deterministic, source-grounded curriculum fixture named
`rl_verifiable_reasoning_v1`. It is both an explorer acceptance fixture and a
real learning path, not synthetic graph-shaped filler.

The topic starts from the question: "How can reinforcement learning reward a
reasoning system when the outcome is mechanically checkable?" It connects:

- MDP, policy, return, exploration, and policy-gradient basics;
- reward models versus verifiable outcome rewards;
- graders/verifiers for mathematics, code execution, and constrained answers;
- group-relative optimization as a comparison mechanism;
- the DeepSeek-R1 distinction between pure-RL reasoning experiments and the
  later readability/alignment pipeline;
- failure modes: reward hacking, weak verifiers, distribution shift, verbosity,
  and apparent reasoning without robust generalization;
- evaluation concepts such as pass@1, pass@k, calibration, and counterexamples.

The fixture should contain approximately 70--100 knowledge nodes, 140--220
binary relations, 8--15 hyperedges, 12--18 short source documents, and at
least three deliberate tensions or counterexamples. Every factual node and
relation must carry one or more source-span references. Educational summaries
are authored or paraphrased; the fixture must not store long copied paper
passages.

Required learner journeys:

1. Query "Why do verifiable rewards help?" and follow from verifier to outcome
   reward, policy optimization, and a concrete code-test example.
2. Query "Does RL create reasoning?" and see both the DeepSeek-R1 claim and
   competing evidence/limitations as separate, cited nodes.
3. Select a `group_relative_optimization` hyperedge and inspect the roles of
   prompt, candidate responses, reward signals, and comparison group without
   mistaking it for pairwise relationships.
4. Filter by source date or evidence status to see which claims are primary,
   derived, disputed, or provisional.
5. Pin `verifier` and `reward_hacking`, then expand one hop to understand why
   a stronger score can still be a worse learning signal.

The fixture's acceptance data includes expected lens snapshots, expected
selection explanations, provenance assertions, tombstone handling, hyperedge
rendering data, and performance budgets. It must run without a live LLM or
network so it can be used in CI and visual regression tests.

This uses the DeepSeek-R1 paper as a study object, not as a mandate to train
the viewer with RL. The relevant product lesson is narrower: make discovery
and learning outcomes measurable with verifiable checks, while preserving
evidence, uncertainty, and counterexamples rather than optimizing an opaque
engagement score.

## Delivery Plan

### Slice 1: Contract And Fixture

- Define Pydantic/JSON schemas for semantic-lens request, snapshot, display
  node/edge/hyperedge, participation, explanation, delta, and error payloads.
- Build a deterministic projection adapter from scoped Kogwistar reads.
- Add the RL curriculum fixture and contract tests for identity, provenance,
  graph-space isolation, tombstones, and hyperedges.
- Run the new application contract fixtures through the installed Kogwistar
  facade without changing or separately validating either core implementation.

### Slice 2: Read API And Query Evaluation

- Add hybrid anchor retrieval with explicit score/explanation fields.
- Add bounded traversal, scoring, display budgets, pinning, and deterministic
  selection.
- Add snapshot watermarking and recent-lens caching.
- Test repeatability, no cross-workspace leakage, boundedness, and delta
  correctness.

### Slice 3: Explorer MVP

- Create a separate frontend package and local dev command.
- Implement canvas, idea bar, evidence drawer, pins, breadcrumb, accessibility
  fallback, and reduced-motion behavior.
- Use Sigma.js plus Worker-backed layout; implement hyperedge glyphs before
  claiming hyperedge support.
- Add Playwright interaction/performance tests against the deterministic
  fixture, not a live LLM run.

### Slice 4: Live Change Awareness And Review Actions

- Add change-feed freshness indicators and deliberate lens refresh.
- Add explicit review/maintenance command entrypoints that return
  authoritative event acknowledgements.
- Add saved/shareable redacted lenses and observability dashboards.

## Acceptance Criteria

- A lens is reproducible for the same request and source watermark.
- No workbench action can mutate authoritative graph state without an explicit
  acknowledged command.
- `no_change` is a valid investigation outcome and creates no fabricated graph
  mutation.
- Every factual node, edge, answer, wisdom artifact, and visible projection
  item has direct grounding or an explicit, scope-safe lineage to grounded
  evidence or operational history.
- Conversation and workflow artifacts retain actor/run/step/input/output
  lineage even when they do not have document spans.
- Cross-workspace, cross-namespace, and stale-revision lineage is rejected.
- Hyperedges retain first-class identity and are never silently converted into
  pairwise facts.
- A query or click changes only a bounded display graph and preserves pins.
- Investigation history is queryable after restart and references its lens,
  source watermark, selected entities, and outcome.
- The RL fixture supports all learner journeys offline and passes visual,
  contract, grounding, and query-selection regression tests.
- All workbench tests use the installed Kogwistar facade without a Kogwistar
  source change.
- Existing Obsidian output and rebuild behavior are unchanged.

## Conflict And Ambiguity Resolution

- **Viewer versus workbench:** the workbench includes viewing, querying,
  investigation, and optional validated editing. The viewer is not a separate
  architecture or storage layer.
- **Grounding versus spans:** source claims require spans; conversation,
  workflow, and execution-wisdom artifacts use their appropriate operational
  lineage. No artificial spans are invented.
- **Proposal versus mutation:** an answer or proposal never changes graph truth;
  only an acknowledged existing Kogwistar command can do so.
- **Codex versus deterministic mode:** these are orchestration modes sharing one
  lens, evidence, validation, and command contract.
- **Workbench versus Obsidian:** the workbench is an additional application
  projection. This ADR changes no Obsidian sink or vault semantics. The sink
  may be changed only if a separately reproduced existing sink bug is found;
  workbench requirements are not a justification for sink changes.
- **Core versus product:** this implementation changes no Kogwistar core. It
  consumes existing reads, vector queries, events, projections, mutations,
  provenance, and namespace contracts. Any core enhancement is out of scope.

## Obsidian Compatibility

This decision does not change the Obsidian sink, its file mapping, its
incremental/full-rebuild behavior, or its ownership of Markdown projection.
Only a separately reproduced pre-existing sink defect may be fixed during this
work; new workbench behavior must adapt to the existing sink contract.
The workbench is an additional application projection and may coexist with an
Obsidian vault for the same workspace. Both are rebuilt from authoritative
Kogwistar state; neither is derived from the other.

## Consequences

This adds a new product surface and an explicit graph-workbench projection and
command contract.
It also adds observability and visual-regression responsibility. In return, it
gives llm-wiki a genuinely exploratory interface rather than a static vault or
debug renderer, while keeping Kogwistar's provenance and append-only semantics
visible instead of flattening them away.

## Non-Goals

- Rendering the complete graph by default.
- Replacing the Obsidian sink.
- Letting browser-local state become a write authority.
- Treating embedding proximity as evidence or truth.
- Training an RL model as part of the initial viewer work.
- Porting product UI policy into Rust core.

## Operating Settings Console

The workbench includes an authenticated operator settings console backed by
`/api/settings` and `/api/settings/health`. It reports effective runtime values
separately from non-secret desired values stored under the application data
directory. Local Chroma text embedding remains the always-on knowledge plane;
the Docker Qwen3-VL Embedding Service is an optional multimodal route.

Only the multimodal retrieval route is live-toggleable in the initial console.
Provider/model changes are staged and require a graceful restart. Embedding
profile changes also require an isolated projection and re-embedding; the UI
never mutates vector dimensions or bypasses the profile guard. High-risk
operations remain explicit confirmation actions, and personal mode may use the
same view without an ACL identity.
