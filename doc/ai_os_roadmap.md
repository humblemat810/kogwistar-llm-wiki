# Executable Plan: Kogwistar â†’ Production-Ready + AI-Native Operating System

> Two-track plan. Track A (Polish) can ship incrementally in weeks.
> Track B (AI OS) is a multi-month architectural build, ordered by dependency.
> Each item has: goal, concrete deliverables, acceptance criteria, and estimated effort.

---

## Track A â€” Polish to Production-Ready

*Goal: make the ecosystem adoptable by developers who have not read every design doc.*

---

### A1 â€” Publish kogwistar to PyPI

**Why first:** Every other repo bootstraps from it. PyPI presence eliminates the GitHub-install dependency in all downstream packages.

**Deliverables:**
- `pyproject.toml` version pinned to `1.0.0` or `0.x` stable
- GitHub Actions workflow: `release.yml` that runs on tag push, builds wheel, publishes to PyPI via Trusted Publisher (OIDC, no stored secrets)
- `CHANGELOG.md` with first entry capturing current state
- Matching releases for `kogwistar-obsidian-sink` and (once API stable) `kg-doc-parser`

**Acceptance criteria:**
- `pip install kogwistar` works on a clean Python 3.11 venv
- `pip install kogwistar-obsidian-sink` pulls the correct version of kogwistar as a dep
- `kogwistar-llm-wiki`'s `pyproject.toml` can declare `kogwistar>=1.0.0` as a real dependency

**Effort:** ~1 day per repo (3 repos total: ~3 days)

---

### A2 â€” Cloistar: Close the Phase 5 Test Debt

**Why:** The governance layer is functionally complete. Tests are the documentation of that claim.

**Deliverables:**
- `tests/unit/test_governance_plugin.py` â€” plugin hook unit test: before_tool_call returns allow/block/requireApproval correctly
- `tests/integration/test_bridge_endpoints.py` â€” bridge endpoint tests for `/policy/before-tool-call`, `/events/after-tool-call`, `/approval/resolution`
- `tests/e2e/test_governance_e2e.py` â€” one smoke test: OpenClaw hook â†’ bridge â†’ decision â†’ back

**Acceptance criteria:**
- `pytest tests/` passes on a clean local install
- `allow`, `block`, `requireApproval` are each covered by at least one test case
- Approval resume flow has at least one test that goes full cycle

**Effort:** ~2â€“3 days

---

### A3 â€” Unified Bootstrap + One-Command Setup

**Why:** Three repos have three different bootstrap scripts. Someone starting fresh must run three commands in the right order.

**Deliverables:**
- `scripts/setup-workspace.sh` at the root of a proposed `kogwistar-workspace` meta-repo (or at the top of any of the main repos) that:
  1. Checks Python version (`>=3.11`)
  2. Creates `.venv`
  3. Calls each repo's `bootstrap-dev.sh` in dependency order
  4. Runs smoke tests to verify the install
- A `Makefile` or `justfile` with targets: `setup`, `test`, `daemon-start`, `daemon-stop`
- Windows PowerShell equivalent: `scripts/setup-workspace.ps1`

**Acceptance criteria:**
- A developer with only `git` and Python installed can go from zero to a running `llm-wiki daemon maintenance` in one command
- Works on macOS, Linux, Windows (Git Bash / PowerShell)

**Effort:** ~1â€“2 days

---

### A4 â€” Init System / Supervisor for Daemons

**Why:** The two daemons are started manually. On a restart they are gone.

**Deliverables:**
- `scripts/kogwistar-llm-wiki.service` â€” systemd unit file for the daemon pair (Linux)
- `scripts/cogwistar-llm-wiki.plist` â€” launchd plist (macOS)
- `scripts/start-daemons.ps1` â€” PowerShell script that starts both daemons as background jobs with restart policy (Windows)
- A `KernelSupervisor` class in `daemon.py` that wraps both daemons, monitors their health via `threading.Event` heartbeats, and restarts them if they stop unexpectedly

**Acceptance criteria:**
- On Linux: `systemctl start kogwistar-llm-wiki` starts both daemons; `systemctl status` shows health
- On any OS: if `MaintenanceDaemon` crashes, it restarts within 5 seconds
- Graceful shutdown drains in-flight work before exiting

**Effort:** ~2 days

---

### A5 â€” Metadata Key Stabilisation Across the API

**Why:** The `metadata.X` vs bare `X` inconsistency in `where` clause queries causes bugs (already fixed in `kogwistar-llm-wiki` but may exist elsewhere).

**Deliverables:**
- Audit of all `get_nodes(where={...})` calls across all repos â€” grep for `"metadata.X"` patterns
- A `WhereClause` typed helper or linter rule that enforces bare keys
- Release notes documenting the canon: bare keys, not `metadata.X`

**Acceptance criteria:**
- `grep -r '"metadata\.' src/ tests/` returns zero results in all repos
- New tests use the canonical form

**Effort:** ~1 day

---

### A6 â€” kg-doc-parser: Complete the Refactor

**Why:** The README explicitly says "being extracted and consolidated into the main kogwistar repository â€” treat as staging area."

**Deliverables:**
- Decision: keep as standalone CLI or merge into `kogwistar[ingestion]` optional extra
- If standalone: stabilise the public API (`run_ocr_source_workflow`, `run_layerwise_source_workflow`), write a stable `__init__.py` export
- If merged: PR into kogwistar with the workflow-ingest code under `kogwistar/ingest/`
- Remove "work in progress" disclaimer from README once the above is done

**Effort:** ~3â€“5 days depending on decision

---

## Track B â€” AI-Native Operating System

*Build order is determined by dependency. Each layer depends on the one above it.*

---

### B1 â€” Token / Resource Budget Accounting

**Dependencies:** None (standalone concern)

**What:** Every workflow run has a declared budget. Every LLM step debits from it. The run is interrupted (with a `RunSuspended` outcome) if the budget is exhausted.

**Deliverables:**
- `kogwistar/runtime/budget.py`
  - `BudgetLedger` â€” per-run token counter, persisted as a graph node in the workflow engine
  - `BudgetExhaustedError` â€” raised when a step tries to call LLM with zero budget
- Integration into `WorkflowRuntime.run()`:
  - Accept `token_budget: int | None` in `run()`
  - Pass `BudgetLedger` via `_deps`
  - Step resolvers call `ctx.deps.budget.debit(tokens_used)` after each LLM call
- `kogwistar/runtime/cost_ledger.py` â€” workspace-level cost accumulator
  - Writes a `budget_event` node per debit (append-only)
  - Queryable: `engine.read.get_nodes(where={"artifact_kind": "budget_event", "workspace_id": ws})`
- Governance integration: the `cloistar` bridge checks `BudgetLedger.remaining` before returning `allow` â€” returns `block` with reason `"budget_exhausted"` if empty

**Acceptance criteria:**
- `WorkflowRuntime.run(workflow_id=..., token_budget=1000)` raises or suspends when 1000 tokens are consumed
- `budget_event` nodes are queryable after a run
- A test proves a budget-exhausted run does not proceed

**Effort:** ~3 days

---

### B2 â€” Graph-Native Message Bus on CDC Oplog

**Dependencies:** B1 (budget debits need a bus to propagate alerts)

**What:** Turn the CDC oplog into a real message bus that any workflow, service, worker, daemon, or external client can publish to and subscribe from, without going through HTTP.

**Deliverables:**
- `kogwistar/bus/message_bus.py`
  - `MessageBus` â€” thin wrapper around the CDC oplog
  - `publish(topic: str, payload: dict, workspace_id: str)` â€” writes a `bus_message` node to the oplog
  - `subscribe(topic: str, since_seq: int, workspace_id: str)` â€” yields new `bus_message` nodes via a polling generator
  - Topics are just strings (e.g., `"distillation.completed"`, `"projection.requested"`, `"budget.alert"`)
- Dead-letter namespace: messages that fail delivery N times are moved to `conv:dead_letter`
- Integration: `MaintenanceDaemon` and `ProjectionDaemon` publish `daemon.heartbeat` messages every poll cycle
- Integration: `ProjectionWorker` subscribes to `promotion.completed` instead of polling the queue

**Acceptance criteria:**
- Publishing a message then subscribing with `since_seq=publish_seq` returns the message
- Heartbeat messages appear in the graph at expected intervals
- Dead-letter test: force a consume error N times â†’ verify node appears in dead_letter namespace

**Effort:** ~4 days

---

Branch-aligned note:

- Workflow is what runs. Runtime is how it runs. Service health is which
  long-running operational process is alive.
- This branch does not recommend introducing a universal agent or capability
  registry before the narrower service, workflow, job, lane, and governance
  surfaces are proven insufficient.

---

### B3 â€” Operational Identity Mapping And Service Visibility

**Dependencies:** B2 (coordination events flow through the bus)

**What:** Make the existing operational identities easier to inspect and relate
without introducing a universal identity node. The goal is an operator-facing map
across service supervision, service health, runs, jobs, and lane messages.

**Deliverables:**
- operator and recovery docs that clearly map:
  - `workflow_id`, `run_id`, `job_id`, `message_id`, `workspace_id`,
    `namespace`, `user_id`, and `token_id`
  - supervised service definitions and service-health identities for
    long-running operational daemons
- startup and recovery reporting that cross-links those identities instead of
  inventing a new actor ontology
- optional service startup/stop events that reference existing service and run
  identities directly

**Acceptance criteria:**
- after `llm-wiki daemon maintenance` runs, operators can connect service
  supervision, service health, runs, and durable jobs without relying on a new
  universal identity node
- recovery and service docs explain the same identity boundaries as the code

**Effort:** ~3 days

---

### B4 â€” Capability Governance Kernel

**Dependencies:** B3 (operational identity map), B1 (budget)

**What:** Build a narrower capability-governance surface for approval,
inspection, and revocation without assuming the next step must be a graph-native
capability governance surface.

**Deliverables:**
- a unified inspection surface for workflow/tool/device capability grants and
  revocations
- governance integration that can answer:
  - what is currently allowed
  - what was denied
  - what changed and why
- optional backing storage choices may include graph truth, named projections,
  or service/kernel rows, but the roadmap does not force a capability-node
  ontology first

**Acceptance criteria:**
- operator views show granted and revoked capabilities with provenance
- governance can block work based on revoked capability state without relying on
  a daemon self-registration step

**Effort:** ~3 days

---

### B5 â€” Real-Time Perception / Environment Sensors

**Dependencies:** B2 (sensors publish to the bus)

**What:** The system reacts to its environment without being asked. Documents that appear in a watched folder are automatically ingested.

**Deliverables:**
- `kogwistar_llm_wiki/sensors/filesystem_watcher.py`
  - `FilesystemWatcherDaemon` â€” wraps `watchdog` (cross-platform file event library)
  - On `file_created` or `file_modified` in watched directories: publishes `"ingest.trigger"` bus message
  - Debounces rapid changes (1-second window)
- `kogwistar_llm_wiki/sensors/webhook_receiver.py`
  - Minimal FastAPI app that receives POST to `/event` and publishes to the bus
  - Can receive events from: GitHub webhooks, email relay, calendar notifications
- `kogwistar_llm_wiki/daemon.py` â€” add `FilesystemWatcherDaemon` to the daemon roster
- `scripts/kogwistar-llm-wiki.service` (from A4) includes the watcher daemon

**Acceptance criteria:**
- Dropping a `.md` file into the watched folder triggers ingestion within 5 seconds (no manual CLI call)
- The ingestion event appears in the graph with correct provenance
- Rapid file writes (10 files in 1 second) produce 10 ingestion jobs, not 100

**Effort:** ~3 days

---

### B6 â€” Workflow Revision Proposer + Approval Gate

**Dependencies:** B3 (proposer needs operational identity context), B4 (revision is a governed capability), B1 (proposals consume budget)

**What:** The system reads `execution_wisdom` nodes and proposes concrete mutations to workflow designs, gated by human approval.

**Deliverables:**
- `kogwistar_llm_wiki/worker.py` â€” add `_step_propose_workflow_revision`:
  - Reads `execution_wisdom` nodes with failure patterns
  - For each pattern: generates a `WorkflowRevisionProposal` node (append-only)
  - Proposal contains: `target_workflow_id`, `proposed_change` (structured dict: add step, increase retry, change threshold), `evidence_run_ids`, `confidence`
  - Does NOT apply the change â€” only writes the proposal
- `kogwistar/runtime/revision_engine.py`
  - `WorkflowRevisionEngine` â€” reads proposals, applies them to `WorkflowDesignArtifact` nodes after approval
  - `apply_revision(proposal_id)` â€” validates, tombstones old design node, writes revised design node
  - `reject_revision(proposal_id)` â€” tombstones the proposal
- `cloistar` integration: `WorkflowRevisionProposal` nodes trigger `requireApproval` in the governance layer before `apply_revision` is called
- Replay validator (v1): re-runs the last 10 failed executions against the proposed new design in memory and reports whether the change would have improved outcomes

**Acceptance criteria:**
- After N failures of `distill` step, a `WorkflowRevisionProposal` appears in the graph
- No revision is applied without a `workflow_approval` node being present first
- Applied revision produces a new versioned `WorkflowDesignArtifact` with backlink to old version
- Replay validator reports: "N/10 past failures would have been prevented"

**Effort:** ~5â€“7 days

---

### B7 â€” Kernel Scheduler

**Dependencies:** B1 (budget), B2 (bus), B3 (operational identity mapping), B4 (capability governance kernel)

**What:** A coordinator that knows what work is pending, what executors or
services are available, and schedules runs by priority and resource budget.

**Deliverables:**
- `kogwistar/kernel/scheduler.py`
  - `KernelScheduler` â€” reads pending jobs from the graph + bus
  - Priority queue: `"critical"` > `"high"` > `"normal"` > `"background"`
  - Budget-aware: refuses to schedule a run if the workspace's remaining token budget is < minimum for that job type
  - Concurrency control: max N simultaneous runs per workspace (configurable)
- Dispatches runs to registered executor instances via the bus
- `kogwistar/kernel/executor.py`
  - `WorkflowExecutor` â€” receives dispatch messages, runs `WorkflowRuntime.run()`, publishes `run.completed` / `run.failed` bus messages
- Integration: `MaintenanceDaemon` and `ProjectionDaemon` become passive executors, scheduled by `KernelScheduler` instead of self-polling
- `llm-wiki kernel start` CLI command â€” starts the scheduler as the system's main process

**Acceptance criteria:**
- Two pending jobs with different priorities: high-priority runs first
- Budget-exhausted workspace: scheduler defers all runs until budget is refilled
- Scheduler crash: in-flight runs are resumable from checkpoint (existing kogwistar `resume_run` mechanism)

**Effort:** ~5â€“7 days

---

### B8 â€” Real-Time Learning Feedback Loop

**Dependencies:** B6 (revision proposer), B7 (scheduler triggers distillation after every run)

**What:** Online learning â€” user corrections and run outcomes feed back into the wisdom layer immediately, not batch.

**Deliverables:**
- `kogwistar/learning/online_feedback.py`
  - `FeedbackEvent` â€” a graph node: `artifact_kind: "feedback"`, fields: `polarity` (+1/-1), `target_node_id`, `feedback_note`, `submitted_by_user_id`
  - `FeedbackCollector.submit(target_id, polarity, note)` â€” writes feedback node
- Integration: `kogwistar-chat` adds a ðŸ‘/ðŸ‘Ž button next to every assistant response â†’ calls `FeedbackCollector.submit`
- Integration: `MaintenanceDaemon` runs `_step_distill` immediately after any `feedback` event is published to the bus (instead of waiting for the next poll cycle)
- Embedding drift detector (v1): every 100 new KG nodes, re-query the 10 most-referenced `execution_wisdom` nodes and check if their nearest neighbours have shifted significantly. If drift detected, publish `"embedding.drift.detected"` bus message.

**Acceptance criteria:**
- Submitting a ðŸ‘Ž on a chat response creates a `feedback` node and triggers immediate distillation
- Positive feedback increases the confidence score of the cited knowledge node
- Drift detection runs automatically and logs drift events to the graph

**Effort:** ~4 days

---

### B9 â€” Unified Resource Namespace (Virtual Filesystem)

**Dependencies:** B3 (operational identities need stable addresses), B4 (capabilities need stable addresses)

**What:** Every addressable resource in the ecosystem gets a URI that can be resolved to a graph node or filesystem path.

**Deliverables:**
- URI scheme: `kogwistar://{workspace_id}/{namespace}/{kind}/{id}`
  - Examples:
    - `kogwistar://demo/kg/entity/abc123`
    - `kogwistar://demo/workflow/run/run-xyz`
    - `kogwistar://demo/service/maintenance-daemon`
    - `kogwistar://demo/file/obsidian/Concepts/AcmeCorp.md`
- `kogwistar/namespace/resolver.py`
  - `URIResolver.resolve(uri: str)` â†’ `GraphNode | FilesystemPath | WorkflowRun`
  - `URIResolver.list(prefix: str)` â†’ `list[str]` â€” enumerates all URIs under a prefix
- MCP tool: `kg_resolve_uri(uri)` and `kg_list_resources(prefix)` â€” exposes the namespace to any MCP-connected client
- Integration: `derived_knowledge` and `execution_wisdom` nodes add `self_uri` to their metadata on creation

**Acceptance criteria:**
- `URIResolver.resolve("kogwistar://demo/kg/entity/abc123")` returns the correct `Node`
- `URIResolver.list("kogwistar://demo/kg/")` returns all KG entities
- MCP `kg_list_resources` works from a client that has never seen the codebase

**Effort:** ~3 days

---

## Summary Timeline

```
Week 1-2    A1 PyPI publish + A5 metadata key fix + A2 cloistar tests
Week 3      A3 unified bootstrap + A4 init/supervisor
Week 4-5    A6 kg-doc-parser decision + B1 token budget
Week 6-7    B2 message bus + B3 operational identity mapping
Week 8      B4 capability governance kernel + B5 filesystem watcher
Week 9-10   B6 workflow revision proposer
Week 11-12  B7 kernel scheduler
Week 13     B8 real-time learning
Week 14     B9 unified resource namespace
```

---

## Definition of Done â€” AI-Native OS

The system qualifies as a genuine AI-native OS when:

- [ ] Any document dropped in a watched folder is automatically ingested, promoted, and projected without human input (Gaps B5 + A4)
- [ ] The system's own workflow designs are revised based on failure patterns, gated by governance approval (Gap B6)
- [ ] Multiple independent workflows or services can coordinate through the graph without shared HTTP session state (Gap B2 + B3)
- [ ] Every LLM API call is metered against a declared budget and the budget is enforceable (Gap B1)
- [ ] Any resource (node, file, run, service) has a stable URI and is discoverable via a common query surface (Gap B9)
- [ ] A developer types one command and a fully operational system starts, supervises itself, and restarts crashed components (Gaps A3 + A4 + B7)
- [ ] User feedback on an assistant response triggers immediate re-distillation without any CLI command (Gap B8)




