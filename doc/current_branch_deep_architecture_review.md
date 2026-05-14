# Current Branch Deep Architecture Review

Review date: 2026-05-10

Scope:

- `kogwistar` core substrate in `kogwistar/`
- `kogwistar-llm-wiki` app layer in `src/kogwistar_llm_wiki/`
- sibling integration repos present in this workspace: `kg-doc-parser/`, `kogwistar-obsidian-sink/`, `kogwistar-chat/`, and `cloistar/`
- branch semantics added in this conversation: durable queue facade, lane-message projection repair, runtime lane sends, run-registry/SSE lane lifecycle, core recovery coordinator, knowledge policy protocols, service health registry, and recovery tutorial material

This review is intentionally architecture-focused. It looks for semantic drift, boundary violations, idempotency/recovery gaps, test gaps, and places where the current implementation weakens the "AI operating system" direction by duplicating concepts or over-frameworking.

## Executive Verdict

The branch is moving in the right direction. The strongest parts are:

- Kogwistar core now owns durable generic mechanics: jobs, lane-message serving rows, projection repair, recovery reports, service-health latest state, and policy protocols.
- `kogwistar-llm-wiki` still mostly owns app taxonomy, namespace layout, maintenance kinds, promotion behavior, inbox names, and Obsidian-specific meaning.
- Recovery remains bounded and operator-visible rather than becoming a hidden scheduler.
- Service health was correctly narrowed away from a universal agent/actor registry.
- The new recovery tutorial documents the at-least-once and projection-repair model clearly.

The main architecture risks are:

- App-level idempotent convergence is not yet strong enough to make at-least-once delivery "look exactly-once" operationally.
- Some core recovery inspection surfaces are not workspace/namespace isolated enough.
- There are now two core "service" concepts with overlapping naming: `server.service_daemon.ServiceSupervisor` and `engine_core.service_health.ServiceHealthRegistry`.
- Package metadata and bootstrap behavior do not match the actual runtime imports and Python version requirements.
- Several docs still describe broad agent/capability registries that conflict with the newer semantic-conservation direction.

## Method

I inspected:

- repo boundary docs: `doc/responsibility_matrix.md`, `doc/repo_boundary_and_contract_catalog.md`
- root app code: `src/kogwistar_llm_wiki/*.py`
- core engine code: `kogwistar/kogwistar/engine_core/*.py`, runtime, server run registry, and service daemon model
- sibling package metadata and public README surfaces
- current tests around recovery, service health, lane messaging, projection, knowledge derivation, worker orchestration, and policies

I also ran one local probe to confirm duplicate-ingest behavior:

```powershell
.venv\Scripts\python.exe -c "... build_in_memory_namespace_engines; run same sync ingest twice; print promoted ids and projection job count ..."
```

Observed result:

```text
first promoted_entity_id != second promoted_entity_id
projection job count = 2
```

This confirms the idempotency concern in Findings F1.

## Current System Shape

### Repository Roles

`kogwistar`

- Owns graph truth, append-only entity events, metastore-backed projections, workflow runtime, run registry, durable job primitives, lane-message mechanics, ACL/capability primitives, service health mechanics, and recovery reporting.
- Should not own llm-wiki artifact names, promotion thresholds, inbox meanings, maintenance kinds, Obsidian rendering, or app namespace layout.

`kg-doc-parser`

- Owns source loading, OCR, page/section parsing, source maps, pointer repair, and document-scoped semantic extraction.
- It now provides direct Python APIs used by `kogwistar-llm-wiki`, especially `parse_page_index_document(...)` and `semantic_tree_to_kge_payload(...)`.

`kogwistar-obsidian-sink`

- Owns graph-to-vault materialization, stable path ledger, markdown/canvas rendering, incremental sync, and safe editable zones.
- It correctly treats the vault as a rebuildable projection.

`kogwistar-llm-wiki`

- Owns app orchestration, workspace namespaces, artifact taxonomy, promotion behavior, maintenance kinds, lane payload meanings, projection manifest meaning, and daemon wiring.
- It composes all sibling repos.

`kogwistar-chat`

- Not directly imported by `kogwistar-llm-wiki`.
- It is an adjacent UI/SSE consumer that depends on Kogwistar server/run-event semantics.

`cloistar`

- Not directly imported by `kogwistar-llm-wiki`.
- It is an adjacent governance repo. Current docs reference it for future AI OS governance/capability direction.

## Invariant Audit

### Graph Truth vs Projection

Status: mostly preserved, with one manifest risk.

Good:

- Lane messages are graph nodes plus projected serving rows.
- `engine.repair_lane_message_projection(...)` rebuilds missing lane rows from entity events or graph fallback.
- Service health uses sparse lifecycle graph facts plus durable latest-health projection rows.
- Obsidian is still a projection, not truth.

Risk:

- Projection manifest rows can mark a promoted id as projected before the vault sync has completed. `ProjectionManager` then trusts manifest ids as the selected projection set without checking readiness.

### Core Mechanism vs App Policy

Status: mostly preserved.

Good:

- Core policy protocols are generic.
- `LlmWikiArtifactTaxonomy` keeps app artifact names in `src/kogwistar_llm_wiki/policies.py`.
- Maintenance kinds remain in app code.

Risk:

- Core `DefaultPromotionPolicy` still encodes `promotion_mode == "sync"`. That is not an app artifact name, but it is still a product-facing mode vocabulary. This may be acceptable as "explicit positive signal" for now, but it is the remaining policy-protocol vocabulary leak to watch.

### Service Health Narrowness

Status: corrected from the previous risk, but needs stronger docs/renaming.

Good:

- `ServiceHealthRegistry` is not a scheduler, actor registry, participant registry, plugin catalog, tool registry, or capability registry.
- It uses `operator_tags`, not capabilities.
- Heartbeats update durable latest state without graph spam.

Risk:

- Kogwistar already has `server.service_daemon.ServiceSupervisor`, which models workflow-backed services, triggers, autostart, restart policy, and child runs. The new `ServiceHealthRegistry` is a separate narrow health registry. The boundary is real but not obvious.

### Recovery Semantics

Status: mostly aligned.

Good:

- `inspect(...)` is read-only.
- `recover_startup(...)` repairs lane projections and service-health projections.
- Auto-resume is off by default and requires an explicit `ResumePolicy` plus `resume_runner`.
- Recovery reports stale service health, expired leases, checkpoints, run history, dead letters, and app surfaces.

Risk:

- `inspect_run_history(...)` ignores the `workspace_id` and `namespace` arguments.
- Service-health projection repair is executed during startup recovery but is not surfaced in `RecoveryReport.actions` or `repaired_count`.
- Checkpoint lookup has a broad fallback that can hide real backend errors and read outside the intended namespace.

### At-Least-Once With Idempotent Convergence

Status: incomplete.

Good:

- Queue and lane mechanics are at-least-once with leases and retry/fail paths.
- Derived/wisdom artifacts use versioned replacement.
- Obsidian sink writes are deterministic and ledger-backed.

Risk:

- Sync-ingesting the same source twice creates two different promoted entity IDs and two projection jobs.
- Maintenance reply lane messages are not deduped by `reply_to_message_id + msg_type + correlation_id`.
- Projection manifest writes add ids before successful materialization.

## Findings

### F1. High: Sync ingest is not idempotent for promoted knowledge

Evidence:

- `IngestPipeline._source_document_id(...)` uses a stable id from workspace plus source URI.
- `promote_to_knowledge(...)` calls `_artifact_node(...)` without a `node_id`.
- `_artifact_node(...)` creates `Node(id=node_id, ...)`; when `node_id` is `None`, core `Node` event-id policy creates a new UUID.
- `_enqueue_projection_job(...)` uses the promoted id as part of the job id, so duplicate promoted ids become duplicate projection jobs.

Code references:

- `src/kogwistar_llm_wiki/ingest_pipeline.py:325`
- `src/kogwistar_llm_wiki/ingest_pipeline.py:661`
- `src/kogwistar_llm_wiki/ingest_pipeline.py:720`
- `src/kogwistar_llm_wiki/ingest_pipeline.py:753`
- `src/kogwistar_llm_wiki/ingest_pipeline.py:793`

Why it matters:

- The branch explicitly wants at-least-once delivery to converge through deterministic ids and completion markers.
- Re-ingest, retry, or crash-before-ack can create duplicate promoted nodes and duplicate projection jobs.
- Obsidian path allocation will remain deterministic per `kg_id`, but duplicate `kg_id`s mean duplicate notes or disambiguated note paths for the same source/title.

Recommended correction:

- Give promoted knowledge a stable id, probably `stable_id("kogwistar_llm_wiki.promoted_knowledge", workspace_id, source_document_id)` or a policy-provided identity key.
- Give promotion candidates and candidate links stable ids too, or explicitly model them as event/version artifacts if duplicates are desired.
- Add a durable completion marker for promotion per `workspace_id + source_document_id + promotion_policy_version`.
- Add tests:
  - running identical sync ingest twice yields one promoted id
  - projection queue has one active job
  - Obsidian snapshot has one entity for the promoted source

### F2. High: Maintenance lane replies are not deduped across crash/retry

Evidence:

- `_emit_lane_reply(...)` always calls `send_lane_message(...)`.
- It uses `correlation_id=reply_to_message_id`, but no existing-reply lookup or unique projection constraint prevents duplicate replies.
- The worker marks the request message completed after the reply send, but if the process crashes after the reply write and before job `mark_done(...)`, lease redelivery can run the handler again.

Code references:

- `src/kogwistar_llm_wiki/worker.py:249`
- `src/kogwistar_llm_wiki/worker.py:263`
- `src/kogwistar_llm_wiki/worker.py:277`

Why it matters:

- The official guarantee is at-least-once, but the target operating behavior is idempotent convergence.
- Duplicate foreground replies are highly user-visible.

Recommended correction:

- Before sending a reply, search the lane namespace for an existing lane message with:
  - `reply_to_message_id == request_message_id`
  - `msg_type == reply.maintenance.completed` or `reply.maintenance.failed`
  - `correlation_id == request_message_id`
- If found, reuse/update status instead of creating a new reply.
- Consider core support for optional lane-message idempotency keys.
- Add tests:
  - simulate reply emitted before job ack, re-run job after lease expiry, assert one reply
  - same for failed reply

### F3. High: Recovery run-history inspection is not workspace/namespace isolated

Evidence:

- `RecoverySubsystem.inspect_run_history(...)` deletes `namespace` and `workspace_id`.
- It lists all server runs from the metastore.

Code references:

- `kogwistar/kogwistar/engine_core/recovery.py:318`
- `kogwistar/kogwistar/engine_core/recovery.py:321`
- `kogwistar/kogwistar/engine_core/recovery.py:375`

Why it matters:

- Recovery reports are operator surfaces, but workspace isolation still matters in multi-workspace deployments.
- The rest of recovery accepts namespace lists. Run history is the outlier.

Recommended correction:

- Filter by `conversation_id`, `workflow_id`, `storage_namespace`, or workspace metadata in server run rows.
- If server run rows do not carry workspace/namespace, add derived filtering from conversation id conventions or add metadata at run creation.
- Add tests:
  - two workspaces with server runs in one metastore
  - `engine.recovery.inspect(workspace_id="a")` does not report workspace `b`

### F4. High: Package metadata and bootstrap do not match actual runtime imports

Evidence:

- Root `pyproject.toml` says `dependencies = []`.
- It comments that `kg-doc-parser` is not a Python import dependency.
- `src/kogwistar_llm_wiki/ingest_pipeline.py` imports `kg_doc_parser` at module import time.
- `src/kogwistar_llm_wiki/projection.py` and `src/kogwistar_llm_wiki/models.py` import `kogwistar_obsidian_sink` at module import time.
- Root `pyproject.toml` says `requires-python = ">=3.11"`, while `kogwistar` requires `>=3.12`, `kogwistar-obsidian-sink` requires `>=3.12`, and `kg-doc-parser` declares Python `^3.13`.
- `scripts/bootstrap-dev.sh` installs sibling repos with `pip install --no-deps -e`, then installs llm-wiki whose dependency list is empty.

Code references:

- `pyproject.toml:11`
- `pyproject.toml:30`
- `pyproject.toml:35`
- `pyproject.toml:58`
- `src/kogwistar_llm_wiki/__init__.py:1`
- `src/kogwistar_llm_wiki/ingest_pipeline.py:15`
- `src/kogwistar_llm_wiki/projection.py:9`
- `src/kogwistar_llm_wiki/models.py:11`
- `scripts/bootstrap-dev.sh:65`

Why it matters:

- A clean install can succeed but import/runtime can fail.
- Python version claims are inconsistent across the composed system.
- New cautious users following docs may hit avoidable setup failures.

Recommended correction:

- Either declare local path dependencies under dev extras and Git URL dependencies for installable mode, or make heavy sibling imports lazy.
- Align root `requires-python` with the strictest direct runtime import, currently likely `>=3.13` unless `kg-doc-parser` lowers its requirement.
- Remove the false "not a Python import dep" comments.
- Avoid `--no-deps` in bootstrap unless there is a separate dependency-install step.
- Add tests:
  - clean venv import smoke: `python -c "import kogwistar_llm_wiki"`
  - CLI smoke: `llm-wiki --help`

### F5. High: Core has two adjacent service concepts that need explicit separation

Evidence:

- `kogwistar/kogwistar/server/service_daemon.py` defines `SERVICE_PROJECTION_NAMESPACE = "service_registry"`, `ServiceDefinition`, and `ServiceSupervisor`.
- `ServiceSupervisor` includes triggers, autostart, restart policy, child workflow runs, and heartbeat.
- `kogwistar/kogwistar/engine_core/service_health.py` defines `SERVICE_HEALTH_PROJECTION_NAMESPACE = "service_health"`, `ServiceDefinition`, and `ServiceHealthRegistry`.
- The two `ServiceDefinition` names are distinct classes with overlapping terms.

Code references:

- `kogwistar/kogwistar/server/service_daemon.py:14`
- `kogwistar/kogwistar/server/service_daemon.py:47`
- `kogwistar/kogwistar/server/service_daemon.py:110`
- `kogwistar/kogwistar/server/service_daemon.py:207`
- `kogwistar/kogwistar/engine_core/service_health.py:19`
- `kogwistar/kogwistar/engine_core/service_health.py:27`
- `kogwistar/kogwistar/engine_core/service_health.py:63`

Why it matters:

- The recent semantic guidance says not to add a scheduler, supervisor, or universal actor ontology in service health.
- `ServiceSupervisor` already is a scheduler/supervisor-like server feature.
- Without a clear boundary, contributors may accidentally merge health visibility with service orchestration.

Recommended correction:

- Rename or alias classes for clarity:
  - `ServiceHealthDefinition` in engine core
  - `WorkflowServiceDefinition` in server service daemon
- Add an ADR or docs section: "ServiceHealthRegistry vs ServiceSupervisor".
- Ensure recovery reads service health only; it must not call supervisor tick/start/restart.
- Add tests:
  - recovery report includes `service_health` rows but does not call `ServiceSupervisor.tick()`
  - service health registry does not create `service_registry` rows
  - service supervisor does not create `service_health` rows unless explicitly bridged

### F6. Medium/High: Projection manifest is updated before successful materialization

Evidence:

- `ProjectionWorker._handle_projection_job(...)` calls `_record_projection_manifest(..., status="rebuilding")` before vault sync.
- `_record_projection_manifest(...)` appends `promoted_entity_id` to `projected_ids` regardless of status.
- On failure, it records status `rebuilding` again, leaving the id in the manifest.
- `ProjectionManager._load_projection_manifest_ids(...)` returns manifest `projected_ids` without checking readiness or materialization status.

Code references:

- `src/kogwistar_llm_wiki/projection_worker.py:48`
- `src/kogwistar_llm_wiki/projection_worker.py:83`
- `src/kogwistar_llm_wiki/projection_worker.py:100`
- `src/kogwistar_llm_wiki/projection_worker.py:121`
- `src/kogwistar_llm_wiki/projection.py:36`
- `src/kogwistar_llm_wiki/projection.py:115`

Why it matters:

- A failed projection can make the manifest look selected even though vault materialization did not complete.
- Recovery may report manifest/vault separately, but snapshot selection already trusts the manifest.

Recommended correction:

- Separate `desired_projected_ids`, `ready_projected_ids`, and `failed_projected_ids`, or only add to `projected_ids` after successful sync.
- Make `_load_projection_manifest_ids(...)` status-aware.
- Add tests:
  - simulate sink failure
  - manifest status is not `ready`
  - snapshot does not treat failed id as successfully projected unless operator override says so

### F7. Medium/High: Workflow design lookup errors can leave jobs in DOING without retry/fail accounting

Evidence:

- The Chroma missing-embeddings fallback is now narrowed, which is good.
- For unrelated workflow lookup errors, `_handle_job(...)` re-raises before entering the runtime try/except that calls `retry_or_fail(...)`.
- `process_pending_jobs(...)` does not catch around each job; the exception bubbles to daemon poll-cycle handling.

Code references:

- `src/kogwistar_llm_wiki/worker.py:30`
- `src/kogwistar_llm_wiki/worker.py:183`
- `src/kogwistar_llm_wiki/worker.py:184`
- `src/kogwistar_llm_wiki/worker.py:234`
- `src/kogwistar_llm_wiki/worker.py:247`

Why it matters:

- It is correct not to silently rematerialize real storage/ACL/query corruption.
- But the claimed job then remains `DOING` until lease expiry instead of immediately recording a retry/failure.
- Operators see a daemon failure, but the queue row gets weaker failure provenance.

Recommended correction:

- Wrap `_handle_job(...)` with a job-level try/except that records `retry_or_fail(...)` for unexpected errors, while still re-raising or reporting the daemon error if desired.
- Keep the narrow rematerialization behavior.
- Add tests:
  - unrelated workflow lookup error does not rematerialize
  - job row records retry/fail
  - failed lane reply is emitted only if a lane request exists and dedupe rules hold

### F8. Medium: Service-health projection repair is not represented in recovery actions/counts

Evidence:

- `recover_startup(...)` calls `service_health.repair_projection(workspace_id=workspace_id)`.
- The result is discarded.
- `RecoveryReport.repaired_count` and `scanned_count` only summarize lane projection repairs.
- `_repair_actions(...)` only creates `repair_lane_projection` actions.

Code references:

- `kogwistar/kogwistar/engine_core/recovery.py:186`
- `kogwistar/kogwistar/engine_core/recovery.py:188`
- `kogwistar/kogwistar/engine_core/recovery.py:150`
- `kogwistar/kogwistar/engine_core/recovery.py:676`

Why it matters:

- Startup recovery is allowed to repair missing service-health latest rows.
- Operator reports should say that this happened.

Recommended correction:

- Add `repaired_service_health_projections` to `RecoveryReport`, or add a generic `RecoveryAction(action_kind="repair_service_health_projection")`.
- Include service health scanned/repaired counts in logs.
- Add tests:
  - deleting service-health projection then `recover_startup(...)` yields a service-health repair action

### F9. Medium: Checkpoint inspection has a broad namespace fallback that hides real storage errors

Evidence:

- `_checkpoint_nodes(...)` tries scoped read.
- On any exception, it retries unscoped.
- On any second exception, it returns empty.

Code references:

- `kogwistar/kogwistar/engine_core/recovery.py:460`
- `kogwistar/kogwistar/engine_core/recovery.py:471`
- `kogwistar/kogwistar/engine_core/recovery.py:474`

Why it matters:

- This can hide ACL, namespace, query, or backend corruption.
- It can also read checkpoints outside the namespace.

Recommended correction:

- Restrict fallback to known recoverable materialization/read-index conditions.
- Return a `RecoveryFinding` for checkpoint-inspection failure.
- Add tests:
  - known missing-index condition falls back
  - unrelated exception becomes a finding or propagates, but does not silently become "no checkpoints"

### F10. Medium: Wisdom source query depends only on namespace, not workspace metadata

Evidence:

- `LlmWikiWisdomPolicy.source_query(...)` deletes `workspace_id`.
- It returns `{"entity_type": "workflow_step_exec"}`.
- Tests currently assert this behavior.

Code references:

- `src/kogwistar_llm_wiki/policies.py:138`
- `tests/unit/test_llm_wiki_policies.py:38`

Why it matters:

- `write_execution_wisdom_artifacts(...)` does read inside `source_namespace=ns.conv_bg`, so namespace usually scopes correctly.
- If namespace scoping is wrong, broad source query can admit cross-workspace traces.
- Workspace metadata filtering is cheap defense in depth.

Recommended correction:

- Include `workspace_id` in workflow step execution metadata, if not already consistently present.
- Make wisdom source query return both `entity_type` and `workspace_id`.
- Add tests:
  - two workspaces with failure traces in shared engine
  - wisdom job for workspace A ignores workspace B

### F11. Medium: Projection snapshot reads all KG edges

Evidence:

- `ProjectionManager.build_projection_snapshot(...)` filters nodes by `workspace_id`.
- It reads edges with `where={}`.
- It then includes relationships only when endpoints are visible ids.

Code reference:

- `src/kogwistar_llm_wiki/projection.py:35`

Why it matters:

- Endpoint filtering reduces the correctness risk.
- But the read can become expensive, and edge ids could collide or carry cross-workspace metadata if future graph IDs become less workspace-stable.

Recommended correction:

- Filter edges by `workspace_id` where possible.
- If Kogwistar edge APIs cannot query that efficiently, add a projection manager comment/test explaining endpoint-filter isolation.
- Add tests:
  - workspace B edge does not appear in workspace A projection even if labels overlap

### F12. Medium: AI OS docs still recommend broad agent/capability registries

Evidence:

- `doc/ai_os_gap_analysis.md` still lists an agent registry and agent identity nodes.
- `doc/ai_os_roadmap.md` still contains a detailed persistent agent identity and capability registry plan.
- The current branch direction explicitly rejected participant/actor/agent/capability registry semantics for this slice.

Code references:

- `doc/ai_os_gap_analysis.md:70`
- `doc/ai_os_gap_analysis.md:160`
- `doc/ai_os_roadmap.md:186`
- `doc/ai_os_roadmap.md:212`

Why it matters:

- Future contributors may reintroduce the exact overbroad abstraction the branch corrected.

Recommended correction:

- Update roadmap/gap docs to distinguish:
  - existing identities: workflow_id, run_id, job_id, message_id, namespace, user_id, token_id
  - narrow service health for long-running operational daemons
  - separate governance/capability kernel work, only when actually needed
- Use the sentence:
  - "Workflow is what runs. Runtime is how it runs. Service health is which long-running operational process is alive."

### F13. Medium: CLI docs and implementation disagree on `KOGWISTAR_DATA_DIR`

Evidence:

- CLI module docstring says persistent commands expect `KOGWISTAR_DATA_DIR` or `--data-dir`.
- Argument parser default is `None`.
- `_build_engines(...)` raises if `data_dir is None`.
- I did not find code reading `KOGWISTAR_DATA_DIR`.

Code references:

- `src/kogwistar_llm_wiki/__main__.py:21`
- `src/kogwistar_llm_wiki/__main__.py:53`
- `src/kogwistar_llm_wiki/__main__.py:267`
- `doc/cli_reference.md:17`
- `doc/cli_reference.md:123`

Why it matters:

- This is user-facing setup drift.

Recommended correction:

- Either implement env fallback or remove the claim.
- Add CLI tests:
  - no `--data-dir` but env var set works
  - no `--data-dir` and no env var gives clear error

### F14. Medium: `doc/cli_reference.md` programmatic API snippet is stale

Evidence:

- The snippet uses `IngestPipeline(workspace_id="demo")`, but `IngestPipeline` expects a `NamespaceEngines` bundle.

Code reference:

- `doc/cli_reference.md:95`

Why it matters:

- Cautious users will copy it and fail immediately.

Recommended correction:

- Replace with:

```python
from kogwistar_llm_wiki.ingest_pipeline import build_persistent_namespace_engines, IngestPipeline

engines = build_persistent_namespace_engines("logs/llm_wiki_data")
pipeline = IngestPipeline(engines)
```

### F15. Medium: Regular ingest and demo ingest have different KG shape

Evidence:

- Demo path calls `persist_demo_graph_extraction(...)` to mirror a richer semantic tree into KG.
- Regular `run(...)` promotes one app artifact node with summary text.

Code references:

- `src/kogwistar_llm_wiki/__main__.py:113`
- `src/kogwistar_llm_wiki/ingest_pipeline.py:416`
- `src/kogwistar_llm_wiki/ingest_pipeline.py:661`

Why it matters:

- The quick demo shows a richer graph than production sync ingest.
- This may be intentional, but the product semantics should be explicit.

Recommended correction:

- Decide whether regular ingest should promote the semantic tree, a summary entity, or both.
- If only demo mirrors the tree, document it clearly in architecture and quickstart.
- Add tests:
  - regular sync ingest KG shape is deliberate
  - demo KG enrichment remains demo-only

### F16. Low/Medium: `DefaultPromotionPolicy` still owns a mode string

Evidence:

- Core test name says default promotion requires `sync`.
- `DefaultPromotionPolicy.decide(...)` directly checks `promotion_mode == "sync"`.

Code references:

- `kogwistar/kogwistar/policy/__init__.py:75`
- `kogwistar/tests/core/test_knowledge_policy_defaults.py:17`

Why it matters:

- The artifact vocabulary leak was fixed, but mode vocabulary is still shared.
- It may be acceptable today, but it is still app-shaped.

Recommended correction:

- Consider changing core default to read a generic explicit signal:
  - `metadata["promotion_approved"] is True`
  - or a `PromotionContext.explicit_positive_signal: bool`
- Let `LlmWikiPromotionPolicy` map `promotion_mode == "sync"` into that signal.

### F17. Low/Medium: `JobQueueSubsystem.enqueue(...)` silently no-ops when metastore lacks queue support

Evidence:

- It returns an empty string if `meta_sqlite.enqueue_index_job` is absent.

Code reference:

- `kogwistar/kogwistar/engine_core/jobs.py:46`

Why it matters:

- For an optional subsystem this can be convenient.
- For llm-wiki durable workers it would hide a broken backend configuration.

Recommended correction:

- Add a strict mode or raise a typed `JobQueueUnavailable` exception from app-critical paths.
- Add tests:
  - llm-wiki startup fails fast if durable job queue is unavailable

### F18. Low/Medium: Service-health repair reconstructs coarse state, not latest heartbeat freshness

Evidence:

- Heartbeats intentionally do not append graph events.
- `repair_projection(...)` rebuilds latest-health rows from sparse lifecycle events.
- For `service.instance_started`, `_apply_event_payload(...)` sets `last_seen_ms` from `started_at_ms`.

Code references:

- `kogwistar/kogwistar/engine_core/service_health.py:356`
- `kogwistar/kogwistar/engine_core/service_health.py:641`

Why it matters:

- This is semantically consistent with "heartbeat is latest projection, not graph truth".
- But after latest projection loss, a repaired service can immediately appear stale because the most recent heartbeat was intentionally not in graph truth.

Recommended correction:

- Document this explicitly in tutorial and recovery docs.
- Optionally use lifecycle event `ts_ms` as the latest sparse observation time for start/stop/stale/recovered events.
- Keep heartbeat spam out of graph.

### F19. Low: Workspace namespace helper still carries legacy projection policy

Evidence:

- `WorkspaceNamespaces.is_kg_visible(...)` remains even though projection policy now owns visibility decisions.

Code reference:

- `src/kogwistar_llm_wiki/namespaces.py:42`

Why it matters:

- It is small, but it can invite new call sites to bypass policy objects.

Recommended correction:

- Remove it if unused, or deprecate with a comment pointing to `LlmWikiProjectionPolicy`.

### F20. Low: Lane anchor nodes use `lane_actor` wording

Evidence:

- `LaneMessagingService` now creates anchor nodes with `artifact_kind="lane_anchor"` and
  preserves legacy `lane_actor` ids when they already exist.

Code reference:

- `kogwistar/kogwistar/messaging/service.py`

Why it matters:

- This is lane-local sender/recipient anchoring, not a universal actor registry.
- The live contract now uses anchor wording, but legacy ids still exist in historical graphs.

Recommended correction:

- Keep the compatibility bridge for old graphs.
- In docs, describe them as "lane sender/recipient anchors", not actors/participants.

## Test Coverage Assessment

Strong existing coverage:

- lane message send/projection/claim/requeue/ack
- lane projection repair and rebuild
- runtime `ctx.send_lane_message(...)` sync/async parity
- run registry `worker.requested` event surfacing
- recovery queue/lane/checkpoint/run/dead-letter report basics
- service health declare/start/heartbeat/stale/repair basics
- app daemon startup recovery and service health declaration
- policy default/app taxonomy boundary
- recovery tutorial docs integrity

Important missing tests:

- duplicate sync ingest converges to one promoted entity and one projection job
- duplicate maintenance replay converges to one foreground reply
- recovery run history is workspace-isolated
- service-health projection repair appears in recovery actions
- checkpoint inspection does not silently swallow unrelated exceptions
- failed projection job does not mark manifest id as ready/projected
- wisdom source query is workspace-filtered
- CLI `KOGWISTAR_DATA_DIR` behavior
- clean-venv import and bootstrap dependency smoke
- service health registry and service supervisor do not accidentally cross-write or cross-mutate

## Recommended Next Slices

### Slice 1: Idempotent Convergence Hardening

Goal:

- Make at-least-once behavior operationally close to exactly-once for llm-wiki-visible outputs.

Work:

- stable promoted knowledge ids
- stable promotion candidate/candidate link ids
- reply lane-message dedupe by `reply_to + msg_type + correlation_id`
- job-level completion markers
- duplicate ingest/retry tests

Why first:

- This directly affects user-visible duplicate notes/replies.
- It strengthens the OS recovery story without claiming exactly-once.

### Slice 2: Recovery Isolation And Reporting Hardening

Goal:

- Make recovery reports trustworthy in multi-workspace/shared-metastore environments.

Work:

- filter run history by workspace/namespace
- surface service-health repair actions
- narrow checkpoint fallback and add findings
- add workspace-isolation tests

Why second:

- Recovery is now a core OS surface. Operator trust depends on isolation and auditability.

### Slice 3: Service Semantics Deduplication

Goal:

- Prevent `ServiceHealthRegistry` and `ServiceSupervisor` from drifting into one blurry "service framework".

Work:

- rename models or add explicit docs
- add non-interference tests
- update `doc/diagrams.md`, `kogwistar/docs/service_daemon_model.md`, and recovery docs with the distinction

Why third:

- It protects the semantic-conservation principle.

### Slice 4: Packaging And Bootstrap Correctness

Goal:

- Make the composed repo install/import path honest.

Work:

- align Python version metadata
- fix dependency comments
- add dependency install step or remove `--no-deps`
- add clean venv import smoke
- make heavy optional sibling imports lazy if needed

Why fourth:

- This reduces onboarding friction and prevents false confidence from local editable environments.

### Slice 5: Projection Manifest Readiness Model

Goal:

- Make projection manifest state accurately reflect materialized vault state.

Work:

- split desired vs ready ids
- status-aware manifest reads
- projection failure tests
- recovery report that distinguishes manifest drift from vault drift

Why fifth:

- Projection correctness is central to the human-facing wiki.

### Slice 6: AI OS Roadmap Semantic Conservation Sweep

Goal:

- Update planning docs so future slices do not reintroduce universal agent/capability registries by accident.

Work:

- rewrite `doc/ai_os_gap_analysis.md` and `doc/ai_os_roadmap.md` sections around agent registry/capability registry
- map proposals onto existing identities first: workflow_id, run_id, job_id, namespace, projection, graph/oplog, ACL/policy
- keep capability/governance work separate from service health

Why sixth:

- The code is now narrower than the old roadmap. The roadmap should stop pulling contributors back toward frameworkification.

## Quality Improvements

### Use typed exceptions for known backend repair edges

The current worker checks `"Missing Embeddings"` in exception text. This is a useful narrow fix, but string matching is brittle.

Recommended:

- define a core/read-layer typed exception for missing embedding materialization
- or add a helper predicate in Kogwistar core
- keep llm-wiki from knowing Chroma error text

### Make recovery reports more structured for operators

Recommended:

- include repair actions for every repairable surface
- include inspection failures as findings
- add summary counts by surface
- include whether the report is read-only inspect or mutating startup recovery

### Strengthen app surface reconciliation

Recommended:

- projection manifest probe should compare manifest ids to actual vault ledger/materialized state
- vault probe should read `System/materialized_state.json` when present
- report "present but stale" separately from "missing"

### Avoid broad `except Exception` in core fallbacks

Recommended:

- fall back only on known recoverable conditions
- otherwise surface a finding or raise
- do not silently turn storage errors into empty reports

### Clarify UoW guarantees by backend

Recommended:

- document that UoW gives best available transactionality
- SQLite/Postgres meta operations are transactional
- backend graph write atomicity depends on storage backend support
- Chroma/in-memory do not carry the same crash persistence guarantees as Postgres

## Documentation Drift To Fix

Root docs needing updates:

- `doc/ai_os_gap_analysis.md`: remove/soften broad agent registry and capability registry plan
- `doc/ai_os_roadmap.md`: replace persistent agent identity slice with narrower service-health and existing-identity mapping
- `doc/cli_reference.md`: fix `KOGWISTAR_DATA_DIR`, daemon-health wording, and programmatic API snippet
- `doc/architecture.md`: "Background Agent System" and UI "Agent" language should be reframed as maintenance daemons/workers unless truly agentic behavior is meant

Core docs needing updates:

- `kogwistar/docs/service_daemon_model.md`: distinguish service supervisor from service health registry
- `kogwistar/docs/recovery_repair_utilities.md`: add service-health repair action semantics once implemented
- `kogwistar/docs/tutorials/20_generic_named_projection_meta_layer.md`: avoid generic examples like `active_agents` unless the tutorial intentionally teaches bridge-governance projection vocabulary

## Architecture Checklist

Use this as a progress tracker for follow-up work.

- [ ] Stable promoted knowledge ids for sync ingest.
- [ ] Stable candidate/review artifact ids or explicit version semantics.
- [ ] Maintenance reply dedupe by reply-to/message type/correlation.
- [ ] Projection manifest distinguishes desired, rebuilding, ready, and failed ids.
- [ ] Recovery run history filters by workspace/namespace.
- [ ] Service-health repair result appears in `RecoveryReport`.
- [ ] Checkpoint inspect fallback is typed/narrow and reports failures.
- [ ] Wisdom source query includes workspace filtering or proven equivalent isolation.
- [ ] Projection snapshot avoids global edge scans or has isolation tests.
- [ ] Core service health and server service supervisor are explicitly separated in code/docs/tests.
- [ ] Root package metadata reflects actual imports and Python versions.
- [ ] Bootstrap installs sibling dependencies or documents a separate dependency step.
- [ ] CLI env var behavior is implemented or docs are corrected.
- [ ] AI OS roadmap stops recommending universal agent/capability registry as the next identity primitive.
- [ ] Clean install/import smoke test exists.
- [ ] Duplicate ingest/retry tests exist.
- [ ] Duplicate reply/retry tests exist.
- [ ] Projection failure manifest tests exist.

## Closing Assessment

The branch has successfully moved several reusable mechanics into Kogwistar core without mostly dragging llm-wiki policy upward. That is the right architectural direction.

The next maturity step is not adding more abstractions. It is making the existing OS substrate more convergent, more isolated, and more honest:

- deterministic ids where repeat work should converge
- explicit versioned replacement where repeat work should create history
- workspace-filtered recovery reports
- service-health visibility that remains separate from service orchestration
- package metadata that matches actual imports
- docs that stop inviting agent/capability frameworkification before the existing identities have been exhausted

The system is close to a clean operating-substrate story. The most important correction is to make at-least-once recovery converge at the app-visible output layer.
