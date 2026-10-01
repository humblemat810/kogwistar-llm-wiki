# Local Bonsai Maintenance Run Report

Updated: 2026-09-30

## Last Authorized Runtime Snapshot (2026-09-29)

The runtime bullets in this section describe the last authorized observation
window only. They do not establish that any container, process, or scheduled
job is still running now.

- PostgreSQL data volumes were preserved throughout the service restarts.
- The maintenance image has since been rebuilt locally as
  `profchan/kogwistar-llm-wiki:v0.5.1`, image digest
  `a184edc272296d578b0226ba38ac3ba9b7aec4dff2d2fed710602347b90d2e55`.
  The maintenance container was recreated from this local image and was
  healthy at that observation; its job budget was 16,000 tokens. A 4,096-token
  reserved output cap was believed to be configured then, but a later audit of
  the current provider call path did not verify that the cap was actually
  passed to the model.
- The maintenance container is healthy and configured to use the OpenAI
  compatible adapter at `http://host.docker.internal:8181/v1`, model
  `Ternary-Bonsai-2-27B-PTQ1_0`.
- The host-side custom llama.cpp process is running from
  `D:\prism-llama.cpp` on port 8181 with the Bonsai language model and its
  matching vision `mmproj` at `D:\models\bonsai2`.
- The server reports an 8,192-token context. A real maintenance inference
  completed with a 3,167-token prompt and 1,024 generated tokens and was not
  truncated. This verifies 8,192 as operational for the current setup; it does
  not establish a larger maximum.
- The authenticated `maintain` MCP request returned queued request
  `f4947681-27ed-5a89-be99-061e9d062b95`. The worker completed it as
  `document_parse_graph` in 146.4 seconds, producing 18 nodes and 17 edges.
  Every extracted node had one mention and one text span. All 17 edges were
  `parent-child` structure; this parse run did not create semantic cross-links.
- The llama.cpp log confirms the worker's provider request and 1,024 generated
  tokens. The stored maintenance trace incorrectly reports `llm_call_count=0`:
  the page-index child summarized usage events but did not add the distinct
  provider-run count. The source fix is now in `longrun_child.py`; it has not
  yet been rebuilt into the running image or verified by a subsequent job.
- Scheduled jobs 483 and 484 completed as deterministic `distill` workflows
  with `call_used=0`. They are not evidence of Bonsai-backed background
  inference. The worker is scheduled, but the current background job kind does
  not call the model.
- The REST service was absent during the initial checks and has since been
  restored and reached healthy status. Authenticated maintenance submission
  works. A subsequent valid read-only `status` call timed out after 30 seconds,
  confirming that the graph read path remains an operator-visibility defect.
- PostgreSQL's active graph tables use `vector(2)`, which is a demo embedding
  layout. No production CPU embedding encoder is running in this minimal
  profile, so retrieval quality from vector similarity is not yet established.
- The existing C-drive Hugging Face cache is
  `C:\Users\chanh\.cache\huggingface`; the Bonsai GGUF weights are stored on
  D. The embedding model's vision projection belongs to its own checkpoint and
  profile. Bonsai's `mmproj` cannot be reused by the embedding model.
- `scripts/start_local_bonsai.ps1` now defaults to the verified 8,192-token
  context and fails fast when required files are missing.

## Evidence Rules

A run is successful only when all of these are observed:

1. `llama-server` exposes `/v1/models` on port 8181.
2. The maintenance container is healthy and has `maintenance.sock`.
3. A direct request completes with `call_used >= 1`.
4. A follow-up request completes within its explicit budget.
5. A scheduled background job invokes Bonsai and records a positive provider
   call count, or records a clearly identified provider failure. A deterministic
   `distill` cycle with `call_used=0` does not pass this condition.
6. The resulting parse and cross-link changes are reviewed before adding more
   stock documents.

Until those conditions are met, deterministic workflow completion is not
evidence that Bonsai performed maintenance.

## Offline Encoder Follow-Up (2026-09-30)

- Downloaded the pinned `sentence-transformers/clip-ViT-B-32` revision
  `327ab6726d33c0e22f920c83f2ff9e4bd38ca37f` into the default Hugging Face Hub
  cache at `C:\Users\chanh\.cache\huggingface\hub`. The 605,157,884-byte
  `0_CLIPModel/model.safetensors` digest matched the pinned SHA-256
  `99d28a652e6ec46629ab7047a0ac82c69b1fe11e0ce672c43af65d3a9a3fc05d`.
- An offline CPU-device inference using the repository venv loaded this exact
  cached checkpoint and returned both a text vector and an image vector, each
  512-D. The installed Torch build is CUDA 12.8, but the model was configured
  and executed on `device=cpu`; this by itself did not verify the CPU-only Torch
  image.
- Built `llm-wiki-embedding:local-cpu-smoke` from
  `Dockerfile.embedding-service` with `LLM_WIKI_EMBEDDING_TORCH_BACKEND=cpu`.
  The standalone package import check passed with Torch `2.8.0+cpu` and
  Transformers `5.17.0`.
- Ran a disposable `--network none` container with the Hugging Face cache
  mounted read-only. The CPU-only image loaded the pinned checkpoint and
  returned both text and image vectors at 512-D. This verifies model inference
  in the CPU image, but not Compose wiring, vector writes, or active graph
  integration.
- A second disposable, network-disabled run exercised the actual FastAPI app
  through `TestClient`: `/readyz` and `/v1/represent` both returned HTTP 200,
  and the text/image responses each contained a 512-D vector. On this Windows
  host, mounting the full Hugging Face Hub root read-only preserves the cache's
  relative snapshot-to-blob symlinks; mounting only the model repo does not.
  The TestClient import emitted a Starlette deprecation warning about `httpx`,
  but the requests succeeded. A real Uvicorn listener and Compose embedding
  service are still not verified.
- `docker compose --env-file doc/local_bonsai_maintenance.env.example
  --file compose.embedding-clip-cpu.yml config --quiet` passed. The standalone
  profile remains CPU-only, capped at 1 CPU/2 GiB by default, publishes only on
  loopback, and mounts the pinned D-drive checkpoint read-only. This validates
  configuration rendering, not container service health.
- The encoder uses CLIP's checkpoint-resident learned text and vision
  projections into one shared semantic space. It does not require a separate
  encoder `mmproj`; the Bonsai `mmproj` remains exclusive to Bonsai's llama.cpp
  image-input path.
- These were local, offline model checks only. No service was started, no
  database/vector store was written, and the existing `vector(2)` graph remains
  unsuitable for this 512-D profile without a separately reviewed graph/profile.

## Offline Feature-Branch CI Follow-Up (2026-09-30)

- On `feat/bonsai-maintenance-review`, the regular provider-free CI marker ran
  under CPython 3.13.3 against the exact Kogwistar, KG Doc Parser, and Obsidian
  sink SHAs pinned in `.github/workflows/ci.yml`: `852 passed, 6 skipped,
  130 deselected` in 499.88 seconds. Pytest cache was disabled, matching the
  repository guidance for this workspace.
- The first local attempts stopped at workflow-layout assumptions because the
  clean feature worktree did not contain sibling dependency checkouts. After
  checking out all three exact workflow-pinned revisions into their expected
  paths, the full selected marker passed. These were temporary local dependency
  worktrees, not changes to vendor pins.
- Targeted maintenance-observation tests also passed (`30 passed`), and Ruff
  passed for the changed implementation/tests. `.test/` remains ignored by
  Git. The feature branch push has no GitHub check run because the regular CI
  workflow triggers on pull requests and `main`, not feature-branch pushes;
  this local run is not presented as hosted CI evidence.
- A follow-up focused run on the pushed tree covered provider configuration,
  observation assessment/persistence, continuation scheduling, adaptive
  background maintenance, and profile state: `60 passed` in 4.64 seconds.
  The Bonsai provider test asserts that the provider chain remains pinned to
  the local OpenAI-compatible endpoint without Ollama fallback.
- The background scheduler's implicit limits remain 4 steps/180 seconds, but
  the dedicated Bonsai profile's explicit 6-step/300-second values are read and
  propagated into queued jobs. Added a regression assertion for both the
  defaults and this override; the focused scheduler test passes. This proves
  payload configuration only, not live worker execution.
- Ran the offline direct/provider/follow-up/scheduler contract selection:
  `7 passed`. It covers the explicit `openai` local endpoint with no Ollama
  fallback, source-ID resolution, bounded direct request defaults, one bounded
  follow-up call, and the profile budget override. These use fakes and do not
  change the live direct/follow-up/background verification statuses below.

## Latest Runtime Follow-up

- The focused maintenance-observation and embedding test set passed locally:
  `76 passed, 3 skipped`; Ruff passed after correcting one import-order issue.
- Observed cycles 494-499 and 502 reached Bonsai at the configured endpoint, but
  their structured critic output was truncated (`LengthFinishReasonError`).
  They correctly failed closed as `quality_unknown` / `request_human_review` and
  created no automatic graph repair.
- Cycle 497 ran the medium-effort critic but still stopped at exactly 1,024
  completion tokens with `finish_reason=length` (3,136 prompt tokens). This
  was initially attributed to the environment variable
  `KOGWISTAR_MAINTENANCE_MAX_OUTPUT_TOKENS=1024`; the local `.env`, Compose
  fallback, and redacted config backup were then raised to 2,048 and later
  4,096. A subsequent source audit found no consumer of this variable in the
  current checkout, so the historical causal attribution and claimed cap are
  unverified and must not be relied upon. Focused tests after the then-current
  diagnostic change passed (`40 passed`), Ruff passed, and Compose validation
  passed.
- Cycle 502 still stopped at exactly 4,096 completion tokens with
  `finish_reason=length`, indicating the real observation prompt can trigger
  excessive internal reasoning even with a reasoning budget. A same-sized
  direct REST probe with `reasoning_effort=none` and the same JSON-schema shape
  completed in 307 tokens with three findings; a separate LangChain structured
  call also parsed three findings successfully.
- Cycle 503 verified the deployed `reasoning_effort=none` change in the real
  scheduled worker. Bonsai completed one call (`input_tokens=3136`,
  `output_tokens=3858`, `duration_ms=127897`), and the schema-valid critic
  returned `weak_label` findings: the `HAS_CHILD` revenue relation lacked
  support, source spans/excerpts were empty or unverified, and one label was
  truncated. The follow-up phase was attempted but correctly skipped because
  the job's 8,000-token budget was exhausted (`token_used=6994`, next estimate
  2379 input + 4096 output). No graph repair was applied. The local background
  job budget was raised to 16,000 after this run.
- Cycle 504 was already persisted with the old 8,000-token limit before the
  maintenance container was recreated. Its first review completed in 88.994s
  (3,102 input, 2,720 output tokens), identified weak/truncated labels,
  unverified grounding and unsupported `HAS_CHILD` provenance, and allowed a
  parent review. The follow-up could not reserve its estimated 2,354 input plus
  4,096 output tokens against the old 8,000-token budget. It failed closed as
  `quality_unknown`; no graph repair was applied.
- Cycle 505 used the new 16,000-token budget and completed two real Bonsai critic
  calls: 3,136 input + 2,923 output tokens in 103.496s, then 3,136 input +
  3,153 output tokens in 121.298s. Both reviews identified weak source
  grounding and unsupported parent/child relation provenance. The job reached
  its configured two-round limit without modifying graph facts. The second
  assessment's audit-lane projection failed with `ValueError`; its assessment
  is visible in worker logs, but this run does not prove durable audit-message
  persistence. The cause was not captured, so do not attribute it to a missing
  Postgres projection implementation or to any specific backend defect.
- Offline investigation found an application-level idempotency hazard in
  observation audit IDs: the prior ID depended on frame and verdict, while the
  persisted payload also includes critic status, findings, and recommended
  action. A repeated assessment of an unchanged frame could therefore reuse an
  idempotency key with a changed payload, which Kogwistar correctly rejects as
  an idempotency conflict. Assessment IDs now hash the canonical full persisted
  assessment identity. A regression verifies exact duplicate assessments retain
  the same ID, changed critic findings receive a different ID, and the
  in-memory lane-message persistence stores both distinct assessments while
  deduplicating the exact retry. Focused tests passed (`30 passed`) and Ruff
  passed. This is a confirmed local correctness fix, but it is not proof that
  this was the cause of the historical Postgres `ValueError`; Postgres-backed
  audit persistence still requires a separately authorized runtime verification.
- Cycle 506 used the new environment but the structured response still reached
  the 4,096-token per-call output ceiling (`finish_reason=length`, 3,102 prompt
  tokens, 4,096 completion tokens). It failed closed as `quality_unknown` and
  did not apply graph changes.
- Cycle 507's first review completed in 139.606s (3,136 input, 3,709 output
  tokens) and independently reported the same missing source excerpts,
  unsupported `HAS_CHILD` revenue relation, unverified grounding, and truncated
  label. Its second review reached the 4,096-token output cap after 191.3s and
  failed closed as `quality_unknown`; the job completed in 251.4s without graph
  mutation.
- Cycle 508 completed both rounds successfully in 80.190s and 69.701s (3,102
  input tokens per call; 2,279 and 1,910 output tokens). Bonsai again detected
  truncated/unverified labels, empty grounding, an unsupported `HAS_CHILD`
  relation, and pointer/base-node granularity mismatch. The job completed at
  its two-round bound without graph mutation. The second result again failed to
  persist to the audit lane with `ValueError`.
- Direct-request verification is still blocked: the direct `maintain` tool call
  with the critic-observed source ID was rejected because that ID does not
  resolve through the user-facing source resolver. A subsequent read-only
  `status` call and topic-based `maintain` calls exceeded both 35-45 second and
  a single 180-second client deadline; no corresponding direct job appeared in
  the maintenance worker queue. The REST process remained CPU-saturated during
  source enumeration. This is a historical runtime observation, not evidence
  about current service state. The source resolver now accepts the active
  revision-document ID as an alias, rejects stale revision IDs, and queues work
  against the canonical logical source ID; focused offline regression tests
  pass. A live direct-request run remains unverified and must wait for a new
  explicitly authorized runtime window. Do not count direct-request maintenance
  as verified until then.
- Resource/health probes briefly marked REST and Grafana unhealthy while their
  container CPU and memory caps were saturated. REST `/healthz` recovered from
  4.7s to 0.3s after a live adjustment to 0.25 CPU / 512 MiB. Grafana's complete
  internal health script passed all components but took 15.0s, exceeding its
  image-default 5s check. The Compose override now gives that script a 20s
  timeout; the running Grafana container was deliberately not recreated, to
  preserve in-memory telemetry. Local config backup records REST 0.25 CPU /
  512 MiB and Grafana 0.25 CPU / 640 MiB for the next recreation.
- Local validation passed: focused maintenance suite `40 passed`, Ruff clean,
  and Compose config validation passed. The local `.env`, Compose fallback,
  `.env.example`, and redacted config backup use a 4,096-token per-call output
  cap and 16,000-token per-job maintenance budget.
- Local maintenance image digest is now
  `a184edc272296d578b0226ba38ac3ba9b7aec4dff2d2fed710602347b90d2e55`.
  This is a local rebuild of the existing tag, not a published image. The
  first real scheduled, model-backed observation is verified. The maintenance
  service has been recreated and cycle 505 verified that its configured second
  provider call can complete. Later calls show occasional output truncation,
  and direct-request behavior remains unverified because the REST/tool source
  resolution path is too slow or cannot resolve the observed source identity.
- PostgreSQL and the maintenance worker remain healthy. REST is now healthy;
  Grafana's actual internal components pass their script, but Docker still uses
  the old image health timeout until Grafana is recreated later with the
  Compose override.
- The running llama.cpp endpoint continues to report `n_ctx=8192`. Host free
  physical memory at the last check was about 770 MiB, so the separate 2-GiB
  CLIP CPU service has not been started; avoid loading it alongside Bonsai until
  there is adequate headroom. The CLIP dual text/image projection profile and
  advertised modalities passed focused local tests, including a fake-model
  contract test that exercises both learned projection methods for text, image,
  and mixed inputs. The focused embedding-service tests now pass (`5 passed`)
  and Ruff is clean; real model inference remains unverified.

## Monitoring-cap update (2026-09-29 16:34 UTC)

The requested ten-hour runtime-monitoring window elapsed at 2026-09-29 07:30 UTC
(goal start recorded as 2026-09-28 21:30 UTC). Monitoring continued past that
deadline and was stopped at 16:34 UTC; this overrun is recorded rather than
presented as compliant. No further periodic polling is part of this run.

## Offline Maintenance Test Follow-Up (2026-09-30)

- The maintenance default test initially failed locally because the installed
  `pytest-dotenv` plugin loads the workspace `.env` before test execution. The
  test cleared the master switch but not the independent background switch, so
  an explicitly configured `LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED=true`
  leaked into the default assertion.
- Updated the test to clear both switches before constructing a new control
  state. No production default or runtime configuration behavior was changed.
- The maintenance/profile/provider/embedding offline unit selection passed:
  `125 passed, 1 warning`. `git diff --check` also passed. These results verify
  deterministic offline contracts only; they do not establish that the expired
  live Bonsai maintenance run is active, scheduled, or healthy.
- Added a checked-in, `slow` in-memory graph integration test for observation
  audit persistence. It enters the same durable workspace namespace context as
  maintenance job dispatch, delivers the same assessment twice, and verifies
  one audit lane message remains in `conv:bg` (`1 passed in 3.32s`). This proves
  app-to-Kogwistar serialization and duplicate-delivery idempotency for the
  in-memory backend only; the historical PostgreSQL `ValueError` remains
  unexplained and unverified after the runtime cutoff.
- Added an exact local Bonsai provider-resolution regression: the configured
  OpenAI-compatible adapter resolves the pinned Bonsai model and localhost-Docker
  endpoint with an empty fallback list. Combined provider, scheduler, control,
  and audit-persistence tests passed (`31 passed in 4.85s`); Ruff and
  `git diff --check` passed. These remain configuration/fake-backend checks,
  not evidence of live model inference.

- At the stop observation, Bonsai llama-server PID 43176 was still
  live on port 8181, and the maintenance container was healthy. The active
  scheduled cycle was 511, started at 16:30:31 UTC. Its first critic call hit
  the 4,096-token output limit after about 203 seconds and failed closed as
  `quality_unknown` / `request_human_review`; no graph repair was authorized.
  The cycle was still active at the monitoring cutoff, so completion and
  subsequent scheduling are not verified.
- The deployed maintenance container was still using image digest
  `a184edc272296d578b0226ba38ac3ba9b7aec4dff2d2fed710602347b90d2e55` at that
  observation. A newer local build exists at digest
  `1450ef21c85f3254f5711f5e822c0d2923e32fb504537e012b8899fc8610471b`, but it
  was not deployed because cycle 511 was active. Do not claim the audit
  idempotency-key change has been verified in the running worker.
- Cycles 509 and 510 both completed and acknowledged their scheduled jobs.
  Cycle 509 had one successful Bonsai assessment and a second call that
  truncated at the output cap; cycle 510 completed two calls. Both found weak
  labels/grounding and relations lacking provenance, and neither changed graph
  facts. Both also logged `maintenance_observation_audit_failed` (`ValueError`),
  so durable observation-audit persistence remains unproven.
- The scheduled cadence was observed continuing through cycle 511, but the
  active cycle prevented a verified completion/schedule-after-completion claim.
  Follow-up maintenance and direct maintenance remain unverified: direct
  requests timed out or failed source resolution, and no direct job was seen in
  the worker queue. The source resolver/pagination path requires investigation.
- The embedding-service fake-model tests prove that text and image inputs use
  their respective projection heads and produce normalized 512-D outputs.
  Real CPU inference remains unverified because available host memory was too
  low to safely load the candidate model beside Bonsai. Maximum usable llama.cpp
  context is also not established; only `n_ctx=8192` has been exercised.

These are explicit outstanding items, not implied successes. Runtime monitoring
must remain stopped after the ten-hour limit; resuming it requires a new user
instruction.

## Direct source resolution code follow-up (2026-09-29)

After stopping runtime monitoring, code inspection found that direct source
lookup only fetched the logical compatibility document. That alias can refer to
an older revision after reingest, even though the ParseView selects a newer
immutable source revision. The resolver now fetches the selected revision by
its exact document ID, avoids graph scans on the successful direct-document
path, and initializes the graph fallback for backends where document lookup
misses. This repairs a concrete stale-alias/control-flow defect, but does not
prove direct maintenance execution against the already-running service.

Validation evidence:

- Existing pipeline-backed direct logical-source lookup plus resolver
  regressions passed together (`3 passed in 115.89s`), after source enumeration
  was narrowed. The isolated cases assert active revision bytes are returned,
  source-node queries are metadata-filtered, and no unfiltered graph scan is
  used.
- Ruff for the changed source and regression test passed; `git diff --check`
  passed.

Still outstanding: execute and verify direct and follow-up maintenance against
the service only if the user explicitly resumes runtime verification; do not
infer it from these unit tests. The output-cap, durable audit persistence,
live embedding Compose/graph integration, and maximum-context gaps remain open.

## CPU encoder and resolver follow-up

- The pinned `sentence-transformers/clip-ViT-B-32` checkpoint is already
  available at `D:\models\sentence-transformers-clip-vit-b32-327ab672`, the
  exact host path used by `compose.embedding-clip-cpu.yml`. Its safetensors file
  is 605,157,884 bytes and its SHA-256 matches the configured pinned digest
  `99d28a652e6ec46629ab7047a0ac82c69b1fe11e0ce672c43af65d3a9a3fc05d`. It is
  now also cached under the default `C:\Users\chanh\.cache\huggingface` tree.
  The Compose profile still mounts the D-drive copy read-only. The checkpoint
  hash is checked by the encoder on startup.
- Source enumeration for topic/status lookup now queries source-graph and
  legacy source-revision metadata separately, deduplicates by node ID, and no
  longer issues an unfiltered `get_nodes(limit=None)` request. Targeted logical
  source lookup also follows the active ParseView to immutable revision bytes.
- The resolver regression suite passes (`2 passed`), the existing pipeline
  lookup path passes (`1 passed`), Ruff passes, and `git diff --check` passes.
  No service was restarted or polled; live direct/follow-up maintenance is
  therefore still unverified.

## Reasoning and context limits

- The official [Bonsai 2 GGUF model card](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf)
  advertises 262,144 tokens and documents `-c` up to 262144 for the custom
  llama.cpp build. Its quickstart uses 32K; the current [known-issues guide](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/blob/main/KNOWN_ISSUES.md)
  suggests 64K for long reasoning/output exhaustion. These are model usage
  recommendations, not memory-fit measurements for this host. The card also
  says the model defaults to `xhigh`, recommends `medium` for shorter reasoning,
  and says `low` behaves like `xhigh`. Thus 262,144 is the model/runtime ceiling,
  not a claim that this 8-GiB host can reserve that context while the 27B weights
  and mmproj are loaded.
- The current launch configuration has a verified working `n_ctx=8192`; that
  is the only local hardware context size supported by observed runtime
  evidence. Larger values were not tested. The maximum operational context for
  this host therefore remains **unknown** and requires an explicitly resumed
  runtime-validation window.
- The observation critic now requests `reasoning_effort="medium"` only when
  its configured parser model name contains `bonsai`; other models retain
  `none`. This follows the model card rather than sending an undocumented
  no-reasoning value to Bonsai. Unit coverage checks both branches. The focused
  maintenance suites pass (`36 passed`), Ruff and `git diff --check` pass. This
  setting is not yet deployed to the running maintenance image, and must not be
  described as a live fix for truncation until verified in a future runtime
  window.
- On 2026-09-30, a read-only `nvidia-smi` snapshot showed 1,122 MiB free on the
  8-GiB RTX 3080 Laptop GPU. This is not a clean model-load baseline, so no
  context experiment was attempted. It reinforces that the operational maximum
  cannot be inferred from the model card and remains unknown pending an
  explicitly resumed runtime-validation window.
- Read-only GGUF metadata inspection confirmed 64 blocks,
  `full_attention_interval=4`, four KV heads, 256-wide key/value vectors, and
  Q4_0 cache. Estimated attention KV is about 18 KiB/token (144 MiB at 8K,
  288 MiB at 16K, 576 MiB at 32K, 1.125 GiB at 64K, and 4.5 GiB at 262K).
  This excludes recurrent state, projector/model residency, CUDA workspaces,
  allocator overhead, and other GPU users; it does not prove those contexts
  fit. It suggests 16K then 32K as cautious future test points only after a new
  runtime-validation window is authorized.

## Offline revalidation (2026-09-30)

- Parsed the local language GGUF header read-only with the matching
  `D:\prism-llama.cpp\gguf-py` reader: `general.architecture=qwen35` and
  `qwen35.context_length=262144`. This confirms the model-declared ceiling
  independently of the upstream model-card text; it does not show that this
  host can load or run the model at that context. Runtime evidence still only
  supports 8,192, and the host's maximum usable context remains unknown.
- The local llama.cpp source checkout is on branch `prism`, commit
  `1a07bfa5f4144274c8f1c9963821dd9d9a51854b`, with a clean worktree. The paired
  Windows binary reports `0.2.0-dev (build 10706, commit 1a07bfa5)`. This was
  checked with `git rev-parse`, `git status`, and `llama-server --version`; no
  model was loaded and no server was started. The mmproj file exists on D: at
  629,246,976 bytes. This records the custom runtime provenance, not a current
  service-health claim.
- The dedicated redacted Bonsai environment backup sets the background cycle
  interval to 300 seconds (five minutes), with background/request maintenance
  enabled. The generic project example and daemon fallback remain 600 seconds;
  both the split and combined Compose overlays pass the setting to
  `--background-interval`, which is forwarded to `MaintenanceDaemon`. The
  five-minute cadence therefore depends on applying the dedicated profile. The
  private `.env` and current runtime were not inspected, so this documents the
  wiring and backup contract rather than claiming the live daemon currently
  uses it.

- Addressed the direct-request identity mismatch in code: an explicit active
  revision-document ID now resolves to its source request, stale revision IDs
  remain rejected after active ParseView resolution, and queue insertion
  canonicalizes to the stable logical source ID. The focused resolver and
  canonical-queue tests pass (`3 passed`); Ruff passes. This does not resolve
  the separate historical topic-enumeration timeout under REST CPU saturation,
  nor verify a live direct request against Bonsai.

- Re-ran the in-memory pipeline acceptance path and source-resolution regressions
  on the current checkout with pytest's cache provider disabled:
  `3 passed in 122.46s`. The acceptance test ingests a source, resolves its
  source ID, submits `maintain` with explicit budgets, and verifies that a
  maintenance job is queued. The two resolver tests verify active immutable
  revision lookup and metadata-filtered source enumeration.
- This confirms the local application request/queue path after the source
  resolver fix. It does **not** show the already-running Bonsai worker executing
  a direct maintenance job, nor does it verify direct/follow-up provider
  completion. No service was queried, restarted, or redeployed; the 10-hour
  runtime-monitoring window remains closed.
- Re-ran the maintenance critic and observation/follow-up unit suites on the
  same checkout: `36 passed in 2.33s`. This verifies deterministic provider
  selection/budget and follow-up payload contracts only; it is not a live Bonsai
  inference or scheduled-cycle result.
- Followed the audit-message write path in code: LLM-Wiki calls Kogwistar's
  `send_lane_message`, and the vendored `EnginePostgresMetaStore` has a concrete
  `project_lane_message` implementation. This disproves the earlier report's
  unsupported claim that the Postgres projection path was incomplete, but does
  not identify the `ValueError` seen in the run. The exception details were not
  retained, and no live database probe was made after the monitoring cutoff.
- Improved future audit-failure traces to include only the innermost source
  basename, line, and function, while still omitting exception text and payload
  data. The regression test asserts that a sensitive exception message is not
  emitted. Re-ran the observation/follow-up tests after this change:
  `36 passed in 0.76s`; Ruff and `git diff --check` passed. This improves the
  next diagnostic opportunity but does not retroactively explain the prior
  Postgres `ValueError`.
- Added a real in-memory conversation-engine integration test that calls the
  audit persistence method twice and confirms exactly one lane-message node is
  stored under the workspace background namespace. It passed (`1 passed in
  16.99s`). This verifies app-to-Kogwistar message serialization and
  idempotency on the in-memory backend only; PostgreSQL behavior remains
  unverified.
- Final combined run of the observation critic and maintenance observation
  suites passed (`37 passed in 11.63s`); Ruff passed after sorting imports, and
  `git diff --check` reported no whitespace errors. Git emitted only a line
  ending conversion warning for the pre-existing `.env.example` worktree file.
- Current upstream Bonsai guidance identifies output exhaustion during internal
  reasoning as a common cause of truncated answers and recommends
  `reasoning_effort=medium` plus a top-level `reasoning_budget_tokens` field for
  moderate output budgets. The local custom llama.cpp source supports both the
  server flag and request field. LLM-Wiki now adds a 2,048-token reasoning
  budget to Bonsai critic calls using OpenAI `extra_body`, retaining the
  4,096-token completion cap and 8,192-token operational context. The focused
  suite passed after this change (`37 passed in 12.06s`); Ruff and
  `git diff --check` passed. This is code-level validation only: no real request
  was sent to the model, and the live maintenance image was not rebuilt.
- Read-only inspection of the pinned CLIP checkpoint metadata on D: found
  `architectures: ["CLIPModel"]`, `model_type: "clip"`, and
  `projection_dim: 512`; the local `0_CLIPModel` directory includes its
  tokenizer and image preprocessor assets. This matches the adapter's expected
  Transformers loader layout, but it does not prove the safetensors load or
  either real projection succeeds.
- Re-ran the embedding-service and standalone API contract suites:
  `11 passed in 1.27s`. These use fake model projections to exercise text,
  image, mixed-input normalization, output dimensions, and profile identity;
  they do not load the real checkpoint or persist vectors to PostgreSQL. Ruff
  passed on the encoder/config/tests, and `git diff --check` found no
  whitespace errors (with only the existing `.env.example` line-ending warning).
- A real bounded inference smoke exposed a Transformers 5.16 API change:
  CLIP feature methods return `BaseModelOutputWithPooling`, with the shared
  projection in `.pooler_output`, rather than a tensor. Updated the adapter to
  accept this output while retaining compatibility with tensor-returning
  versions, and changed the regression fixture to exercise the wrapped output.
  The focused test passed (`1 passed in 1.21s`). The pinned local checkpoint
  then loaded and ran on `device=cpu` using the installed `torch 2.8.0+cu128`
  build (two CPU threads): load 40.63s; text, image, and mixed-input encode
  3.41s total; 3 vectors, each 512-D and normalized. No graph/database write
  or GPU execution was requested. At that time the CPU-only Torch container,
  peak memory, and retrieval quality were not tested; the CPU-only image was
  subsequently verified in the Offline Encoder Follow-Up above. Free host RAM
  was 3.21 GiB before and 2.77 GiB after the process;
  this is a coarse host snapshot, not a peak-RSS measurement.
- Repeated the real-checkpoint smoke through the actual FastAPI app in-process
  with `TestClient`: `/readyz` returned 200 and `/v1/represent` returned 200
  for text, image, and mixed finance-style inputs. All item IDs/order, exact
  profile fingerprint, 512-D dimensions, and unit norms were validated. The
  smoke exited successfully. This covers app request validation and
  serialization with real CPU projections, but it is not a Docker service
  launch and does not attach or write the embedding profile to the active graph.
- Offline direct-maintenance path review found that the `maintain` MCP schema
  did not advertise `maintenance_kind` or its already-supported `parse_target`
  argument. Added both fields and exposed `parse_target` from the authoritative
  `ParseTarget` Pydantic JSON Schema (including the nested revision-pinned
  region contract, rather than an untyped object). Under the global Python,
  which has MCP 1.25.0, lifecycle tests fail during MCP 2.x server construction.
  The project `.venv` has the declared MCP 2.2.0; rerunning the full focused MCP
  lifecycle file there passed (`3 passed in 1.37s`). Ruff and `git diff --check`
  pass. The in-memory `AgentGateway.maintain` end-to-end test also passed
  (`1 passed in 82.26s`), confirming that a request can be queued through the
  gateway. These tests use test infrastructure and do not prove that a direct
  maintenance job reaches Bonsai. Direct request execution against Bonsai
  remains unverified.
- Provider-routing review confirmed the maintenance worker and observation
  critic share the configured maintenance provider settings; the redacted local
  profile uses an `openai` chain containing only `openai`, pointing at local
  llama.cpp rather than Ollama. It also found that the scheduler code fallback
  was 4,000 total tokens while the critic reserves 4,096 output tokens before
  input, even though both checked-in local profiles set 16,000. Aligned the
  runtime fallback to 16,000 and strengthened the scheduler test to assert it.
  The focused scheduler test passed, then the complete adaptive scheduler,
  observation, and critic suites passed (`43 passed in 19.33s`). Ruff and
  `git diff --check` pass. This closes the no-override budget mismatch in code;
  it does not verify the already-stopped live worker or claim new Bonsai calls.
- Added a provider-resolver regression for the redacted local Bonsai settings:
  the maintenance provider resolves to OpenAI-compatible local llama.cpp, with
  the expected model/base URL/key-env name and an empty fallback list. The
  provider configuration suite passed (`10 passed in 1.69s`).
- Direct-maintenance budget review found that the MCP tool made every budget
  optional while omitted call and step limits became zero in the worker ledger.
  Direct requests now receive bounded defaults (2 calls, 16,000 tokens, 6
  steps, 300 seconds) while caller-supplied values remain authoritative. A
  topic request is limited to the number of sources that can each receive the
  configured round allowance; with the default two rounds, it queues one source
  and reports additional matches as skipped instead of splitting the follow-up
  budget across unrelated documents. The direct gateway regression passed;
  the broader agent-capability file had 18 passing tests and one test-only
  assertion used the wrong persisted key, which was corrected and rechecked in
  the focused test. Ruff and `git diff --check` pass. This is queue/budget
  validation only; no live Bonsai request was made and direct provider
  completion remains unverified.
- A follow-up budget audit found the background call-count fallback was one,
  despite `maintenance_max_rounds=2`; the continuation preserves `call_used`,
  so its second Bonsai critic call would be rejected. Raised the default to two
  calls and aligned `.env.example` with the local backup. Added a regression
  proving a continuation with one prior call can use the second call. The
  combined scheduler, observation, critic, and provider-config suites now pass
  (`54 passed in 10.33s`); Ruff and `git diff --check` pass. This verifies the
  bounded follow-up code path with fakes, not a new live Bonsai follow-up.

## Offline verification update (2026-09-30)

- Added a provider-agnostic context-overflow stop path for read-only maintenance
  reviews. Recognized overflow errors persist `critic_status=blocked_context`,
  suppress continuation/remaining job steps, and durably disable autonomous
  and request maintenance with `status_reason=blocked_context_window`, aborting
  the maintenance experiment rather than leaving direct/follow-up requests
  available. Final job failure includes the claim token to prevent stale-worker
  writes. Provider wording is heuristically
  classified, so unrecognized provider errors remain ordinary fail-closed critic
  failures and are not claimed to trigger the persistent stop.
- Source audit correction: the current LLM-Wiki observation critic constructs
  its model through the shared provider factory without setting an output-token
  cap or Bonsai reasoning budget. Earlier report entries describe prior runtime
  observations/experiments and are not evidence that those limits exist in the
  current source path. Provider-specific generation caps, especially across a
  Codex bridge or any future Claude adapter, remain a separate unverified item.
- Regressions cover common context-limit wording, chained exceptions, durable
  full maintenance pause, assessment status, parser-job terminal failure, no
  retry, claim-token fencing, requeue of already-claimed work after abort,
  provider-switch refresh, and no continuation.
  Focused maintenance/provider selection after the full-stop/fencing assertions:
  **63 passed**. The changed files pass Ruff `E402,E9,F` and `git diff --check`.
  A broader daemon-recovery selection was attempted but its seven persistent
  backend cases could not initialize because this isolated environment lacks
  optional `chromadb`; this does not count as a pass for those integration
  tests. This is offline code evidence only; it does not establish current
  Bonsai runtime health.
- The separate KG Doc Parser provider-bounds feature branch's full `ci` marker
  selection completed after adding the optional Anthropic adapter: **66 passed,
  1 skipped, 166 deselected**; provider-limit
  Ruff and diff checks passed. It remains unmerged and is not yet pinned by
  LLM-Wiki.
- The parser feature commit `e9c0fbe` was pushed to
  `feat/provider-generation-bounds`; follow-up `09aec68` adds explicit
  Anthropic context-overflow type coverage. GitHub Actions has not run for that branch:
  the parser workflow is configured for pushes to `main` and pull requests
  targeting `main`, not arbitrary branch pushes. The branch still needs a PR
  before its remote CI status can be verified; the local full marker suite is
  green.
- The LLM-Wiki context-stop/provider-refresh implementation was committed and
  pushed as `9376650` on `feat/bonsai-maintenance-review`. GitHub Actions has no
  run associated with this branch and no pull request is currently associated
  with it; its local focused suite is green, but remote CI remains unverified.

- Re-ran the focused embedding-service suites after confirming that the CPU
  CLIP encoder calls its learned text and image projection heads:
  `python -m pytest tests/unit/test_embedding_service.py
  tests/unit/test_standalone_embedding_service.py -q -p no:cacheprovider`;
  result: **11 passed**.
- Re-ran the maintenance observation/critic, adaptive scheduling, profile, and
  daemon worker-factory suites:
  `python -m pytest tests/unit/test_maintenance_observation_critic.py
  tests/unit/test_adaptive_maintenance.py
  tests/unit/test_maintenance_observation.py
  tests/unit/test_maintenance_profiles.py
  tests/unit/test_maintenance_daemon_worker_factory.py -q -p no:cacheprovider`;
  result: **62 passed**.
- Re-ran the direct maintenance gateway, source resolution, critic dispatch,
  and observation continuation suites:
  `python -m pytest tests/unit/test_agent_capabilities.py
  tests/unit/test_agent_source_resolution.py
  tests/unit/test_maintenance_observation_critic.py
  tests/unit/test_maintenance_observation.py -q -p no:cacheprovider`;
  result: **60 passed in 139.76s**. This exercises the application/provider
  contracts with test fixtures, not a live Bonsai request.
- Ruff passed on the embedding service implementation and its focused tests.
- Ran the cached `llm-wiki-embedding:local-cpu-smoke` image as a disposable
  container with `--network none`, `--memory=2g`, `--cpus=1.0`, and the pinned
  D-drive CLIP checkpoint mounted read-only. Real CPU inference loaded the
  model in **27.111s**, encoded one text and one synthetic image in **19.142s**,
  returned two normalized 512-D vectors, and peaked at **869.4 MiB RSS**. This
  verifies bounded encoder inference under the configured 1-CPU/2-GiB limits;
  it does not establish persistent service behavior, graph profile integration,
  or retrieval quality. The container had no network and no database mount.
- Ran a separate read-only retrieval sanity probe against the five existing
  finance Markdown documents: 42 short text chunks and five manually labeled
  topic queries, using the same pinned CLIP checkpoint, CPU-only, with a
  read-only corpus mount and no network/database. Expected documents ranked
  first for **4/5** queries; the source-grounding/evidence-quality query ranked
  its expected finance-rubric document **third**, behind the AI-chip map and
  ARM watchlist. This tiny, hand-authored probe is biased and not a retrieval
  benchmark; it specifically shows policy/rubric language is not reliably
  distinguished and does not pass the retrieval-quality gate.
- Reviewed existing search primitives before proposing an app-local fallback.
  Kogwistar provides a graph `search_hybrid` path backed by SQLite FTS5 BM25
  plus that engine's vector backend, and a separate `CatalogStore` lexical/BM25
  path that filters entries by ACL before ranking. LLM-Wiki's current finance
  workbench does not call either path for these documents. The CLIP 512-D
  profile is also not attached to the existing `vector(2)` demo graph. Thus the
  retrieval probe above is model-only; wiring this encoder into production
  search requires a distinct correctly dimensioned/profiled projection and an
  explicit app query path, not mixing CLIP vectors into the current graph.
- These checks do not include Bonsai inference or a live maintenance request;
  no Compose service was started and no graph was mutated. They do not change
  the outstanding live-verification statuses below.

## Post-restart Local Readiness Snapshot (2026-09-30)

- The custom llama-server executable and both Bonsai files are present at the
  documented D-drive paths. The executable reports llama.cpp
  `0.2.0-dev`/build `10706`, commit `1a07bfa5f`, MSVC `19.42.34438.0`, x64.
- `scripts/start_local_bonsai.ps1` defaults to `-c 8192`. This is the
  conservative previously exercised operating point, not a proven maximum;
  the actual host maximum remains unknown pending an explicitly authorized
  controlled memory/context ladder.
- A read-only `nvidia-smi` snapshot reports an 8-GiB RTX 3080 Laptop GPU with
  8,016 MiB free and 0% utilization. This is consistent with the user-reported
  post-restart unloaded state, not evidence of successful inference. No server,
  Docker service, or database was started or queried for this snapshot.
- The required assets are present: the language GGUF is 5,946,648,928 bytes and
  the matching vision projector is 629,246,976 bytes. The Windows launch script
  binds `0.0.0.0` for Docker Desktop reachability; firewall scoping remains an
  operator requirement.
- A secret-redacted inspection of the active local `.env` confirms the
  maintenance provider and its only provider-chain entry are `openai`, the
  model is `Ternary-Bonsai-2-27B-PTQ1_0`, the endpoint is
  `http://host.docker.internal:8181/v1`, and the configured background cadence
  is 300 seconds. Secret values were not read or reported. Compose config-only
  validation against `compose.yml`, `compose.memory-agent.yml`, and
  `compose.embedding-clip-cpu.yml` succeeds and resolves PostgreSQL, combined
  REST/MCP/maintenance, Grafana, and CPU CLIP services. No containers were
  started. The environment requests background maintenance, but this does not
  establish the durable maintenance-control state; the context circuit breaker
  may still be latched from the prior run.
- Removed the unused `KOGWISTAR_MAINTENANCE_MAX_OUTPUT_TOKENS` setting from the
  Bonsai environment snippet. The current critic path does not consume it, so
  leaving it there would imply a completion cap that is not implemented.
- Expanded offline regression verification across
  `test_worker_runtime_orchestration.py`, `test_maintenance_job_lowering.py`,
  adaptive scheduling, observation execution/persistence, context abort, and
  provider configuration: **94 passed in 83.34s**. This covers the control and
  queue paths for direct, follow-up, and scheduled work with test providers;
  it is not evidence that Bonsai inference or live PostgreSQL persistence
  succeeds after restart.

## Runtime Recheck After Host Restart (2026-09-30 12:56 UTC)

- Docker Desktop is running. The Compose project had only the maintenance
  container running; its Postgres, REST, MCP, and Grafana containers were
  stopped. The maintenance container had restarted 83 times. Its health check
  was green, but its startup log showed engine initialization failing because
  the hostname `postgres` could not be resolved. A green container health
  check therefore did not mean that maintenance was operational.
- Started only the existing `postgres` Compose service. It became healthy and
  reused the existing `llm-wiki_postgres_data` named volume; no volume was
  removed or recreated. The maintenance daemon subsequently logged that it
  started with a 10-second interval. This proves database/daemon startup only,
  not a completed model-backed maintenance cycle.
- The container's non-secret maintenance settings resolve to the
  OpenAI-compatible provider with chain `openai`, the Bonsai model name,
  `host.docker.internal:8181`, and nonzero bounded background budgets (2 calls,
  16,000 tokens, 6 steps, 300 seconds). No secret values were inspected or
  reported.
- No host llama-server process was running. The custom executable and both
  Bonsai model files are present, and the RTX 3080 Laptop GPU was idle with
  8,192 MiB total memory. Thus no Bonsai inference or new graph writes were
  observed during this recheck.
- The Windows Private firewall profile currently has inbound action `Allow`.
  The current account is not elevated. The checked-in launcher binds to
  `0.0.0.0`, which would expose the unauthenticated local inference endpoint
  on more than the Docker path under that firewall policy. I did not start it
  with that exposure. The supplied `127.0.0.1` binding also cannot be assumed
  reachable from the Compose container. A verified Docker-only host binding or
  an appropriately scoped firewall configuration is still required before
  model-backed maintenance can safely resume.
- Two pytest processes belonging to an already-running unit-CI invocation
  were present and were left untouched. No CI or runtime process was killed.

## Objective Acceptance Snapshot (2026-09-30)

| Requirement | Current evidence | Status |
| --- | --- | --- |
| Bonsai is the maintenance provider, not Ollama | OpenAI-compatible worker configuration and real scheduled Bonsai inference observed through cycle 510; the current live image was not rebuilt with the latest code | PARTIAL |
| Background maintenance is scheduled and completes | Cadence observed through cycle 511; cycle 511 was still active at the monitoring cutoff, so its completion and next schedule are unknown | PARTIAL |
| Direct maintenance reaches provider and completes | In-memory queue path now verifies nonzero bounded defaults and one-source follow-up allowance; no direct Bonsai job was verified in the live worker | UNVERIFIED |
| Follow-up maintenance reaches provider and completes | Background cycle 505 completed a second Bonsai critic call; continuation payload tests pass, but end-to-end follow-up repair/reparse and direct-request follow-up completion are not verified | PARTIAL |
| Bonsai reasoning stays within critic response budget | New 2,048-token request budget is covered by tests and supported by local llama.cpp source; no live inference has tested it | PARTIAL |
| Audit/quality evidence is durable on PostgreSQL | Live audit writes previously raised `ValueError`; in-memory persistence/idempotency now passes, but PostgreSQL root cause and fix are not verified | UNVERIFIED |
| Maximum usable llama.cpp context is known | Local GGUF/model card declare 262,144 tokens; the model card quickstart uses 32K and its known-issues page suggests 64K for long reasoning, but neither establishes fit on this 8-GiB RTX 3080 Laptop. `n_ctx=8192` is the only verified operating point; current free VRAM snapshot was 1,122 MiB and no larger context was tested | PARTIAL (model limit known; host maximum unknown) |
| Small CPU embedding model is usable | Pinned CLIP ViT-B/32 passed CPU-only inference in a 1-CPU/2-GiB, network-disabled container: 869.4 MiB peak RSS, 27.111s load, 19.142s to encode one text and one synthetic image, normalized 512-D outputs; prior in-process FastAPI route test returned HTTP 200. A five-query sanity probe ranked 4/5 expected files first but put the quality-rubric result third. Persistent Uvicorn/Compose service behavior, representative retrieval quality, and active graph integration remain unverified. MobileCLIP-S0 (216 MB) remains a separate-license/loader candidate | PARTIAL |
| Finance parse/cross-link quality passes review | Bonsai repeatedly flagged weak labels, empty/unverified grounding, and unsupported parent-child relations; no graph repair was authorized | FAIL (quality gate not met) |
| Expand the source corpus | The finance review gate failed; no additional documents should be added until reparsing/cross-links are reviewed successfully | HOLD |
| Config and non-log reporting exist | Redacted environment example, operator guide, and this report are present | PASS |

Runtime statuses above refer to the last authorized observation, not current
service health. Do not infer that services or jobs are still running. Resuming
live acceptance checks requires a new explicit runtime-verification window;
the original ten-hour monitoring limit has been honored as a stop condition.

## Runtime Recheck After Resume (2026-09-30 13:39 UTC)

- The llama.cpp server session was still live. Its logs show a completed short
  JSON-format request and `reasoning_budget=2048` activation. This verifies the
  local endpoint can honor that explicit request parameter; it does not prove
  the maintenance provider path supplies the same parameter by default.
- The maintenance container uses the older `profchan/kogwistar-llm-wiki:v0.5.1`
  image. Its scheduled cycle 680 ended `quality_unknown` after a
  length-truncated critic response and failed closed. Cycle 681 completed a
  critic review and one bounded follow-up round, then ended at
  `max_rounds_reached`. The findings still called out weak/empty grounding,
  unsupported parent-child relation evidence, and missing relation provenance.
  No graph mutations or corpus expansion were authorized.
- The durable control file says `background_enabled=false` and
  `request_enabled=true`. I left background work disabled after confirming
  cycle 681 was terminal; no new run was scheduled.
- The container was unhealthy before restart, with a healthcheck that only
  tests for `/var/lib/llm-wiki/maintenance/maintenance.sock`. Restarting only
  the maintenance container preserved the Postgres volume and durable control
  state, but the socket healthcheck remained unhealthy. The mounted directory
  contains a stale `test.sock` from Sep 28, not the expected maintenance
  socket, while the daemon process is alive. Therefore service health and
  control-plane availability remain unverified; do not treat the running
  process as a healthy scheduled worker.
- Current host headroom is low (about 1.25 GiB free RAM; RTX 3080 Laptop GPU
  reports 6,895 MiB used of 8,192 MiB). I did not build an image, increase
  context, or start another background inference.
- On `feat/bonsai-maintenance-review`, the control listener now tolerates
  disconnected clients and stale control instances merge updates against the
  durable state file. Focused Windows verification after the final code edit:
  `15 passed, 1 skipped`; Ruff passed; the PowerShell launcher parsed without
  syntax errors. The Linux socket regression had passed in an isolated
  container before these final documentation/launcher changes. The updated
  launcher selects the WSL virtual-network address instead of binding to
  `0.0.0.0`; it passes a local llama.cpp reasoning effort/budget. This does
  not alter application provider behavior for Codex, Anthropic, or remote
  OpenAI-compatible providers.
- No other feature branch was rebased or modified. The unrelated email test
  relocation remains separate. This work is on the already-existing
  `feat/bonsai-maintenance-review` branch to avoid creating another branch.

### Control Socket Recovery (2026-09-30 13:44 UTC)

The daemon finished startup after the earlier 13:39 snapshot. The container is
now `healthy`; both `maintenance.sock` and the stale `test.sock` exist. A
read-only `maintenance-control --status` request received a successful socket
response (`ok=true`), confirming the listener works after startup. Durable
background mode remains disabled and direct-request mode remains enabled. This
corrects the transient health state above; it does not establish that another
background cycle is scheduled or that direct maintenance has been exercised.

## Bounded Background Recheck And Direct-Request Probe (2026-09-30 14:07 UTC)

- The local llama.cpp endpoint returned HTTP 200 from `/v1/models` when called
  inside the Compose network. It identified the Bonsai GGUF, advertised active
  `n_ctx=8192`, and declared multimodal capability. The live maintenance
  container had provider `openai`, a single-entry `openai` chain, model
  `Ternary-Bonsai-2-27B-PTQ1_0`, and the WSL endpoint
  `192.168.64.1:8181/v1`.
- I enabled one five-minute background interval. Cycle 682 selected the same
  existing weakly grounded finance concept, made two actual HTTP
  `/v1/chat/completions` calls to Bonsai (3,102+3,887 tokens in 131 seconds,
  then 3,102+1,990 tokens in 65 seconds), completed its single bounded
  follow-up, and was acknowledged at `max_rounds_reached`. No context overflow
  or output-length truncation occurred in this cycle.
- The cycle's assessment remained `weak_label`/`review_parent`; its findings
  again include empty or unverified mention grounding, missing parent context,
  and an unsupported `HAS_CHILD` relation without provenance. This confirms
  successful Bonsai execution and follow-up scheduling, but not successful
  parsing/cross-link quality or graph repair. I disabled future background
  scheduling after the cycle; no additional source documents were added.
- The subject's assessment `source_document_id` did not resolve through the
  MCP `source` tool (`exists=false`). A bounded topic-based `maintain` request
  then timed out during source-catalog resolution and returned no job ID.
  Maintenance logs show no resulting direct request job. The read-only
  `status` tool also exceeded the client stream timeout. MCP startup itself
  reached healthy, but its 384-MiB container rose to about 350 MiB during the
  catalog/status work. I stopped MCP after those probes to release memory;
  Postgres and the maintenance daemon remain running. Direct request execution
  against an existing source is therefore still UNVERIFIED, and source
  catalog resolution is a concrete blocker to that acceptance test.
- Durable maintenance state after the cycle is `background_enabled=false`,
  `request_enabled=true`; the maintenance container is healthy. Grafana is
  still stopped, so OTLP export errors continue and observability relies on
  container logs plus persisted control state. The `.test/` directory remains
  ignored (`git check-ignore .test/bonsai-runtime` succeeds).
- The topic-based direct-request timeout was traced to repeated active-source
  catalog scans: `maintain()` resolved topic matches by loading the full source
  catalog, then `_load_source_request()` reloaded that catalog once per match.
  The implementation now loads the catalog once and reuses it for topic
  filtering and request construction. A regression test asserts exactly one
  catalog read for a topic maintenance request; focused gateway tests passed
  (**19 passed**) and Ruff passed at that point. A second live probe with this
  source change mounted read-only into the MCP container still exceeded 120
  seconds without a tool response, HTTP inference request, or queued job. The
  MCP container was stopped; no direct job was created. Thus the repeated scan
  was a real inefficiency but not the only blocker.
- Follow-up code inspection found the source enumeration itself is unbounded:
  it calls `get_nodes(limit=None)` with default embeddings included and then
  performs a named-projection lookup per logical source. Request token/call
  budgets are applied only after this discovery and source hydration. The
  feature branch now bounds topic discovery to 128 graph nodes, requests only
  documents and metadata (not embeddings), and fails with an explicit request
  for source IDs when the workspace exceeds the discovery limit. Explicit IDs
  now resolve each source's active revision directly in the workspace source
  namespace and avoid topic-catalog enumeration. Regression tests cover the
  bound, payload selection, and direct-ID lookup without catalog scans; all 21
  focused gateway tests pass and Ruff passes. The change is not in the running
  image, so live direct maintenance remains unverified.

## Scheduled Bonsai Follow-up (2026-09-30 14:48 UTC)

- Durable control was changed to `background_enabled=true` and
  `request_enabled=true`. The daemon scheduled and claimed cycles 683, 684, and
  685 at its configured five-minute cadence, using only the local Bonsai model
  and existing stock-knowledge nodes. No new source documents were added.
- Cycle 683's first critic call returned HTTP 200 after 99 seconds and
  assessed `weak_label`; its second critic response hit the 4,096-token output
  cap and failed closed as `quality_unknown`. No graph change was authorized.
- Cycle 684 made two successful Bonsai calls (2,136 and 3,000 output tokens),
  completed its follow-up at `max_rounds_reached`, and was acknowledged. Its
  verdict remained `weak_label` due to unverified grounding, unsupported
  `HAS_CHILD` relation provenance, and granularity concerns. No graph change
  was authorized.
- Cycle 685's critic response again hit the 4,096-token output cap, failed
  closed, and was deferred for human review. These observations establish that
  an 8,192-token context can complete successful critic calls and one
  successful follow-up; they do not establish the maximum usable context or
  acceptable parse/crosslink quality. The fail-closed behavior prevented
  unsupported automatic repair.
- The feature branch now limits critic output to four findings, each at most
  200 characters, and asks for no reasoning trace or preamble. Its focused
  regressions pass, but the running container still uses the older v0.5.1
  image, so the change has not yet been tested against Bonsai live.
- Grafana is stopped; OTLP export failures are observability noise and did not
  prevent the maintenance jobs from completing. Direct MCP execution remains
  unverified pending a request against a known source ID.

## Direct MCP And Scheduled Follow-up (2026-09-30 15:03 UTC)

- The MCP container was started with the feature-branch source mounted read-only
  and the existing Postgres data volume. A direct `maintain` request for the
  known logical source `6c0d8870-93b5-5b5b-a43d-51ba8c011787` returned a queued
  job ID (`d2e2142b-a018-5e89-91ef-d083d64853f0`). The maintenance worker later
  claimed it and emitted the terminal acknowledgement; this confirms the MCP
  enqueue-to-worker path, not a successful crosslink mutation. The queue row's
  final persisted status was not independently queried.
- The direct request found no eligible related candidate and completed at
  `max_rounds_reached` without invoking the model or changing the graph. This is
  a successful bounded no-op, not evidence of a newly created relation.
- Scheduled cycle 686 used Bonsai for two critic calls (1,850 and 3,340 output
  tokens). It completed its follow-up and remained `review_required` due to
  empty source grounding, a truncated label, unsupported `HAS_CHILD`, and
  missing relation provenance. Scheduled cycle 687 also completed two Bonsai
  calls (2,954 and 2,306 output tokens), reached `max_rounds_reached`, and was
  acknowledged. Its verdict remained `weak_label` for weak grounding, weak
  labels, and unsupported relation provenance. No graph repair or source
  expansion was authorized.
- Cycle 688 was claimed at 15:02 UTC and was still running at the end of this
  observation window. Durable background and direct-request modes remain
  enabled. This provides evidence that scheduled follow-up and a direct request
  both reach terminal handling, while knowledge quality remains below the
  acceptance bar.
- The active maintenance container still runs the published v0.5.1 image; the
  branch fixes for bounded source discovery and critic output are mounted only
  into the MCP service and have not been deployed to maintenance. Grafana remains
  stopped, so trace-export errors continue; the worker's local logs and durable
  job state are the evidence source.
- The checked-in environment example now reflects the observed scheduled-work
  setup (`background_enabled=true`) and the verified WSL-to-Docker endpoint.
  Scheduled work is limited to reviewing existing sources; add no new source
  documents until parsing and crosslink quality pass review.

## Subsequent Scheduled Cycles (2026-09-30 15:10 UTC)

- Cycle 688's critic response hit the 4,096-token completion ceiling after a
  3,102-token prompt. It failed closed as `quality_unknown`, was deferred for
  human review, and authorized no graph mutation.
- The daemon continued scheduling work: cycle 689 was claimed at 15:08 UTC and
  was still running at this check. This verifies that the deployed v0.5.1 worker
  continues after a critic truncation; the feature branch's context-overflow
  circuit breaker is not yet deployed to that maintenance container.
- Direct MCP maintenance was confirmed through worker logs, but the result was
  a bounded no-candidate completion. Background maintenance is genuinely
  model-backed and scheduled, yet repeated reviews still report weak grounding,
  unsupported relations, or truncated output. Quality therefore remains on
  hold; do not add more stock documents or treat these runs as successful
  parsing/crosslink repair.

### Cycle 689 Completion (2026-09-30 15:14 UTC)

- Cycle 689 completed two Bonsai critic calls (3,194 and 3,385 output tokens),
  reached `max_rounds_reached`, and was acknowledged. It did not truncate, but
  the final assessment was `contradictory`: source spans were empty, the label
  was ambiguous/truncated, and the edge summary referenced base-node IDs rather
  than the curated endpoint IDs. The relation also lacked auditable provenance.
  No graph repair or new source was authorized.
- This shows the model can complete bounded scheduled reviews at the current
  8,192-token server context, while quality is still insufficient for automatic
  repair. It neither establishes the maximum context nor supports corpus
  expansion.

### Runtime And CI Follow-up (2026-09-30 16:13 UTC)

- The maintenance container started at 13:33:53 UTC, but scheduled background
  maintenance was enabled at 14:48 UTC. Anchor the ten-hour observation window
  to that activation and stop at 2026-10-01 00:48 UTC; do not extend it.
- Scheduled cycles 694 and 695 each made two successful Bonsai calls and were
  acknowledged. Both final assessments remained `weak_label` with
  `review_parent` recommendations. Neither authorized graph mutation or source
  expansion. Cycle 696 also completed two calls and was acknowledged with the
  same `weak_label`/`review_parent` outcome. Cycle 697's follow-up failed
  closed and was deferred for human review; cycle 698's follow-up did likewise.
  Neither created graph mutations. Cycle 699 was scheduled at 16:12:33 UTC and
  was active at this observation. These dispatches take roughly 5.5 minutes,
  close to the five-minute cadence; queue emptiness after completed cycles
  indicates no accumulating backlog in the observed window.
- The Bonsai endpoint remains responsive with the 27B model at `n_ctx=8192`.
  A point-in-time GPU sample showed 6,895 MiB of 8,192 MiB allocated and 0%
  utilization; this is an idle sample, not a peak-usage measurement. The
  maximum safe context has still not been established.
- GitHub CI on `4ac9ca8` exposed one shared failure across CPython 3.12-3.14:
  `test_disconnected_control_client_does_not_kill_server` used a socket path
  beneath pytest's deeply nested temporary directory, exceeding Linux's Unix
  socket path limit. The test also raced on socket-file creation before
  `listen()` completed. It now uses a unique short path under the OS temp
  directory and waits until a connection succeeds before intentionally
  disconnecting. The entire test module passes on the Linux PyPy 3.11 image
  (`16 passed`); the changed file's Ruff check, CI's `E4,E7,E9,F` selector, and
  `git diff --check` pass. A broader default Ruff run reports 65 `I001`
  import-order findings across the repository, unrelated to this socket fix.
  Commit `35cdcd8` was pushed and hosted run
  [36740812453](https://github.com/humblemat810/kogwistar-llm-wiki/actions/runs/36740812453)
  completed successfully: the required CPython 3.12-3.14 and PyPy 3.11 tests,
  lint, Rust checks, container smoke, and benchmark jobs all passed. The
  separate optional PyPy 3.12 beta lane remains experimental and is not a
  support gate.
- Grafana remains stopped; failed OTLP exports are noisy but have not prevented
  local worker logs, provider calls, acknowledgements, or scheduling. Quality
  remains below the acceptance bar: keep the no-new-sources hold in place.

### Runtime Follow-up (2026-09-30 16:41 UTC)

- Cycles 699 and 701 completed two successful Bonsai calls each and were
  acknowledged. Cycle 700's critic failed closed and was deferred. Cycle 701
  assessed weak grounding, an unsupported `HAS_CHILD` relation, empty spans,
  and missing relation provenance; it authorized no graph mutation. Cycle 702
  was also safely deferred without a provider call because the available token
  budget could not cover its estimated input plus the reserved completion.
- The branch-built image `kogwistar-llm-wiki:bonsai-35cdcd8` was smoke-imported
  successfully and initially ran as the maintenance container on the existing
  `llm-wiki_app_data` volume. It was later replaced by the rebuilt feature
  image described below. Postgres and MCP were not recreated.
- Startup recovery took several minutes under the `0.20` CPU quota and Docker
  temporarily marked the container unhealthy while waiting for its socket.
  Temporarily raising only that container to one CPU allowed recovery to
  complete; the report recorded 16,842 queues, 351 lane rows, 1,992
  checkpoints, and 1,521 dead letters, with no repairs. This is evidence that
  startup recovery cost is material for this persisted workload and should be
  observed on future recreations; it is not evidence that the model or graph
  is unhealthy.
- The active feature image uses the requested Bonsai-compatible provider
  configuration. Continue validating actual provider-call traces, not merely
  the configured model name. No additional source documents or automatic graph
  repairs are approved until grounding and relation provenance meet the review
  bar. Grafana remains stopped, so OTLP export warnings persist.
- The ten-hour monitoring deadline remains 2026-10-01 00:48 UTC, anchored to
  background-work activation at 14:48 UTC. Do not extend it automatically.

### Bonsai Runtime And Context Follow-up (2026-09-30 17:35 UTC)

- The first container after the `35cdcd8` build did not receive
  `KOGWISTAR_MAINTENANCE_API_KEY_ENV` or the configured local key. The overlay
  now explicitly passes both through. Rendered Compose and the running
  container confirm the Bonsai-compatible OpenAI endpoint, model, indirection
  variable, and non-empty key; the key value is intentionally not recorded.
- A duplicate observation-audit delivery exposed that Kogwistar's optional
  lane-message projection lookup raises `NotImplementedError` on this backend
  even though projection writes are idempotent. LLM-Wiki now tolerates only
  that specific exception when it can verify the exact audit message already
  exists in the workspace's internal conversation namespace. Missing records
  still raise. A regression test covers both cases.
- Cycles 704-707 made real local Bonsai calls. Cycle 704 initially failed
  before inference because the API key was missing. Cycles 706's two requests
  returned HTTP 200 but both ended with `LengthFinishReasonError`; the final
  assessment was `quality_unknown`, the job was acknowledged, and no graph
  mutations occurred. Cycle 707 returned HTTP 200 but the structured critic
  rejected its response with `ValueError`; it also failed closed with
  `quality_unknown` and no graph mutations. Cycle 707 survived container
  recreation in the durable queue and was subsequently claimed and completed
  by the replacement worker. Cycle 705's persisted assessment is also
  `quality_unknown`. No source documents were added.
- Cycle 706 demonstrates the 8,192-token server context can be exhausted by
  large review prompts/responses: llama.cpp reported prompt sizes up to about
  7,500 tokens, and GPU utilization peaked at 96% with 6,895 MiB of 8,192 MiB
  allocated. This is not proof that the model's theoretical maximum is 8,192;
  it is the only currently deployed context setting and the larger review
  frames did not reliably produce valid structured output.
- Background review settings were documented in the Bonsai env snippet but
  were not consumed by the scheduler. The scheduler and Compose overlay now
  honor `LLM_WIKI_MAINTENANCE_BACKGROUND_OBSERVATION_TOKEN_BUDGET` and
  `LLM_WIKI_MAINTENANCE_BACKGROUND_OBSERVATION_NEIGHBORHOOD_COUNT`; the local
  profile uses 1,800 evidence tokens and at most 24 neighbors to reserve more
  room for a useful model response. These are bounded evidence limits, not a
  reduction of the maintenance call budget.
- The context guard now treats provider `LengthFinishReasonError` and explicit
  `finish_reason=length` as capacity failures. It durably pauses request and
  background maintenance rather than repeatedly spending time on truncated
  structured responses. Cycle 707 used a payload queued before this guard and
  before the smaller evidence limits; the next newly scheduled cycle is the
  first production test of both changes.
- The replacement maintenance image was built from the active feature branch
  and deployed only to `llm-wiki-maintenance-1` on the existing
  `llm-wiki_app_data` volume. Its socket is healthy, it has the Bonsai key and
  the `1,800`/`24` limits, and its CPU cap is restored to `0.20`. Postgres and
  MCP were left running. Recovery scanned 355 lanes and 16,945 queues in about
  58 seconds when temporarily allowed one CPU; it reported no repairs. Grafana
  remains stopped, so OTLP exporter warnings continue but have not prevented
  scheduling or Bonsai calls.
- A byte-for-byte verified copy of the active local `.env` was saved outside
  the repository at `C:\Users\chanh\Documents\llm-wiki-bonsai-runtime-backup-2026-10-01.env`.
  It contains private runtime credentials; do not commit or print it.
- Focused verification after these changes: 54 unit tests passed, the CI Ruff
  selector passed, and `git diff --check` passed. The ten-hour observation
  deadline remains 2026-10-01 00:48 UTC; do not extend it.

### Bonsai Runtime Follow-up (2026-09-30 17:43 UTC)

- Cycle 708 was the first scheduled cycle using the reduced background review
  frame (1,800 evidence tokens and at most 24 neighbors). The daemon scheduled
  it, claimed it, renewed its lease during both review rounds, completed the
  plan, and acknowledged the durable job. The container remained healthy.
- The first model-backed observation completed with `adequate`, no findings,
  and no recommended action. A second observation in the same bounded plan
  failed with `ValueError`; the worker correctly recorded `quality_unknown`,
  requested human review, and did not continue into a graph-repair action.
  The logs intentionally record only the exception class because validation
  errors can include untrusted model response content. The failure cause is
  therefore not established as context overflow; unlike cycle 706, this run
  did not report `LengthFinishReasonError`.
- Cycle 708 took about 193 seconds from first dispatch to terminal plan
  completion. This is materially shorter than earlier 5.5-minute cycles but is
  still an individual sample, not a throughput guarantee. No source ingestion
  or automatic graph mutation was authorized by the review result.
- Host GPU utilization sampled at 0% after the cycle completed with about
  6,895 MiB of 8,192 MiB allocated. This is an idle post-call sample and does
  not establish peak utilization or the maximum safe context. Keep the verified
  server context at 8,192; do not increase it based on model-card limits alone.
- Grafana remains stopped and OTLP export continues to warn about unresolved
  `grafana:4318`; local scheduling, inference, and durable acknowledgement were
  unaffected in this cycle. The ten-hour observation deadline remains
  2026-10-01 00:48 UTC; do not extend it.

### Bonsai Runtime And CI Follow-up (2026-09-30 18:23 UTC)

- Cycles 709-712 were all scheduled, processed, and durably acknowledged by
  the healthy maintenance daemon. Cycle 709's two critic rounds failed closed
  with `ValueError`; cycle 710's two rounds completed as `adequate`; cycle 711's
  two rounds again failed closed with `ValueError`; cycle 712 had one
  `ValueError` round and one `adequate` round. Failed rounds returned
  `quality_unknown`/human-review findings. No cycle authorized an automatic
  graph repair or source ingestion, and no new documents were added.
- Cycle durations ranged from about 112 to 190 seconds across these four
  cycles. Their outcomes show that the reduced frame can yield a valid critic
  answer, but does not eliminate intermittent structured-output/validation
  failures. The failures are not proven to be context overflows: their logged
  exception class is `ValueError`, whereas known truncation is classified as a
  context-limit failure. Keep the configured 8,192-token model context and the
  hold on corpus expansion; do not infer a safe larger context or parsing
  quality from a single `adequate` observation.
- The first full local CI attempt used the host's unrelated global Python 3.13
  installation (`mcp 1.25.0`) and failed 11 MCP 2.x tests at server
  construction. A fresh ignored `.test/ci-venv` was then installed using the
  exact CPython dependency sequence from `.github/workflows/ci.yml`; it
  resolved MCP 2.2.0. The full provider-free CI marker passed there:
  `827 passed, 6 skipped, 130 deselected` in 623.66 seconds. The focused
  maintenance regression set, including slow persistence replay, also passed
  (`28 passed`); the CI Ruff selector and `git diff --check` passed.
- `git check-ignore` confirms `.test/` and its venv are ignored. The temporary
  venv is local verification state only and must not be committed.
- At this snapshot the maintenance container is healthy and Bonsai's
  `/health` endpoint responds. Its `/slots` status shows server context 8,192;
  the last host GPU sample was idle (0% utilization, 6,895 MiB allocated), so
  it is not a peak-memory or maximum-context measurement. Grafana remains
  stopped and OTLP export warnings persist without preventing the observed
  provider calls or job acknowledgements.
- Monitoring remains anchored to background-work activation at 14:48 UTC and
  must stop at 2026-10-01 00:48 UTC, with no extension.

### Bonsai Runtime Follow-up (2026-09-30 18:34 UTC)

- Cycle 713 completed and was acknowledged by the healthy daemon. Its first
  critic round returned `ValueError` and was converted to `quality_unknown`;
  the second round returned `adequate` with no findings. The bounded plan
  ended at `max_rounds_reached`, without authorizing graph repair or source
  ingestion. Total dispatch duration was about 189 seconds.
- This further confirms that the current 1,800-token/24-neighbor frame can
  produce valid critic results, while validation failures remain intermittent.
  Because the failed round is only classified as `ValueError`, do not label it
  a context overflow or infer a context-window fix. Continue to require
  evidence-grounded quality before corpus expansion; leave model context at
  the verified 8,192 tokens.
- Commit `2a7335c` passed every triggered GitHub workflow: [required CI](https://github.com/humblemat810/kogwistar-llm-wiki/actions/runs/36758349718), [PyPy 3.11 container smoke](https://github.com/humblemat810/kogwistar-llm-wiki/actions/runs/36758349710), [slot/runtime benchmarks](https://github.com/humblemat810/kogwistar-llm-wiki/actions/runs/36758349749), and [optional PyPy 3.12 beta](https://github.com/humblemat810/kogwistar-llm-wiki/actions/runs/36758350064).

Model-size comparison sources: Apple's [MobileCLIP repository](https://github.com/apple-aiml-research/ml-mobileclip)
describes the image/text model family and inference stack; the [MobileCLIP-S0
checkpoint page](https://huggingface.co/apple/MobileCLIP-S0) reports a 216 MB
checkpoint. This alternative is recorded for evaluation, not adopted.

### Bonsai Runtime Follow-up (2026-09-30 18:48 UTC)

- Cycle 714 was scheduled, processed by the Bonsai-backed worker, and
  durably acknowledged. One critic round failed with `ValueError` and was
  recorded as `quality_unknown`/human review; another returned `adequate` with
  no findings. The bounded plan completed at `max_rounds_reached`; it did not
  authorize a graph repair or new source ingestion. As in prior cycles, the
  failure is not proven to be a context overflow.
- Runtime provenance check found that the maintenance container was created
  from this `feat/local-bonsai-maintenance` worktree's Compose files, while the
  running MCP container has a read-only source bind from the separate
  `feat/bonsai-maintenance-review` worktree. That review branch is an ancestor
  already merged into this active feature branch; it is not a rebase target.
  The source mismatch is recorded because live direct-MCP verification should
  use a single known checkout, but the MCP service was not recreated during
  this observation window to avoid dropping a potentially active bridge
  session. This is not evidence that direct model-backed maintenance has
  passed: the prior live direct request was acknowledged as a bounded no-op
  without a provider call, and that requirement remains open.
- Cycle 714 reinforces the mixed-result pattern: the configured frame can
  produce an `adequate` response, but critic validation is not yet reliable
  enough to authorize corpus expansion. No source documents were added; keep
  the 8,192-token context and existing evidence limits unchanged.
- GitHub's anonymous Actions API returned HTTP 403 during the check for
  workflows triggered by report commit `a1792a4`; the browser fetch also
  returned a cache miss. The earlier code commit `2a7335c` has verified green
  workflows above, but checks for `a1792a4` are not independently verified in
  this observation. Do not treat unavailable status as success.
- At the 18:48 snapshot, the report update was the only local worktree change.
  Monitoring remains time-bounded to 2026-10-01 00:48 UTC.

### Source-Grounded Review Hardening (2026-09-30 19:30 UTC)

- Live cycles 715–718 were scheduled and durably acknowledged. The reviewer
  repeatedly reported `quality_unknown` after structured-output `ValueError`s;
  the graph/source/relation/neighborhood groups were often omitted from the
  bounded observation frame. An earlier cycle-714 `adequate` result therefore
  did not prove that the model had actually seen authoritative source evidence.
- The active feature branch now resolves a reviewed pointer to an immutable
  source entity in the workspace source namespace, then requires exact
  workspace, logical source identity, digest, requested revision/document pin,
  bounded character span, and stored-excerpt agreement before invoking the
  model critic. It scopes
  reads through Kogwistar's namespace adapter, prioritizes the verified source
  record within the frame budget, and fails closed to human review when proof
  is missing. It does not mutate graph truth or source records.
- Raw excerpts are transient model input only. Persisted audit and lane-reply
  frame payloads redact `source_excerpt` while retaining revision, span, and
  digest identifiers. Critic failures now emit a bounded safe error code rather
  than logging untrusted exception text.
- Added negative coverage for cross-namespace pointers, missing workspace
  metadata, content and pinned-digest mismatches, revision/document mismatches,
  unverifiable legacy documents, stale frame evidence, false-adequate
  prevention, and excerpt redaction. The focused maintenance set passed
  `69 passed`; full provider-free CI passed `835 passed, 6 skipped, 130
  deselected` in 661.89 seconds. Full Ruff passes for changed files; the
  repository's CI-critical Ruff selectors and `git diff --check` also pass.
  A repository-wide Ruff run still reports 41 unrelated import-order findings;
  they were not swept into this feature.
- No new finance documents were added. No direct mutation was authorized by
  the background reviews. Cycle 718 acknowledged despite two critic
  validation failures; the current live image does not yet contain the new
  evidence guard, so observations from that image remain historical evidence
  only and must not be treated as source-verified review.
- Runtime configuration check: maintenance chat is configured for
  `openai`-compatible Bonsai at the local llama-server URL and model
  `Ternary-Bonsai-2-27B-PTQ1_0`. No embedding-provider settings are present in
  the running container. The app resolver consequently defaults to its
  deterministic fake 2-D embedder when building a fresh engine. Do not silently
  replace an existing profile: persisted vector-space compatibility has not yet
  been proven. Candidate for a separate, CPU-only profile is BAAI
  `bge-small-en-v1.5` (384 dimensions, 0.067 GB in FastEmbed's supported-model
  table) served by Hugging Face TEI's CPU image and OpenAI-compatible endpoint.
  Sources: [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/),
  [TEI CPU images](https://github.com/huggingface/text-embeddings-inference#docker-images),
  [TEI OpenAI embeddings API](https://huggingface.co/docs/text-embeddings-inference/quick_tour).
  This is a researched candidate, not yet configured or used for this dataset.
- PostgreSQL inspection confirms the live canonical graph schema has
  `gke_nodes.embedding vector(2)`. Therefore a direct switch to the 384-D BGE
  encoder would fail dimensional validation and must not be attempted against
  this existing graph. CPU embeddings require a separately isolated vector
  profile/store or a deliberate, tested migration and re-embedding plan; this
  experiment has not done either.
- Worktree remains on `feat/local-bonsai-maintenance`; `feat/bonsai-maintenance-review`
  remains merged at `7b96d24`. No rebase of other feature branches is required.

### Source-Grounded Review Follow-up (2026-09-30 19:50 UTC)

- Focused validation of the current source-evidence implementation passes:
  `63 passed` across evidence resolution, critic assessment, observation, and
  persistence tests. Changed-file Ruff and `git diff --check` pass.
- Fresh provider-free CI passes against this exact worktree:
  `839 passed, 6 skipped, 130 deselected, 29 warnings` in 697.38 seconds.
- Corrected two test fixtures so corrupted and legacy revision documents retain
  the logical-source identity pinned by the source pointer. This keeps the
  verifier's ordering meaningful: logical-source mismatch is rejected before
  digest checks, while matching identities reach digest/legacy-evidence checks.
- The live maintenance container is still the previously published `v0.5.1`
  image, so its reviews do not exercise the new guard. Cycle 719 returned
  `adequate`, but its frame omitted source, relation, and neighborhood evidence;
  it is not evidence of a grounded review. Cycle 720 completed and was
  acknowledged after two critic failures (`quality_unknown`). Cycle 721
  completed at 19:52 UTC and was acknowledged; its first critic round failed,
  while its second returned `adequate` with all source, relation, and
  neighborhood groups omitted. It is not evidence of grounded quality. Cycle
  722 was then scheduled at 19:58 UTC. No graph mutation or new source ingestion
  was authorized. Grafana/OTLP remains unavailable and generates export
  warnings without stopping the worker.
- The local code change remains uncommitted on
  `feat/local-bonsai-maintenance`. No other feature branch needs rebasing; the
  old `feat/bonsai-maintenance-review` branch is already incorporated here.

### Bonsai Durable Expansion Runtime Check (2026-10-01 23:25 UTC)

- Correction to the earlier branch note: `feat/local-bonsai-maintenance` is the
  active Bonsai branch and contains the host-side email implementation removal
  via merge commit `6316778`; its current code commit is `bad2b40` and is pushed
  to the matching origin branch. Do not rebase this worktree again for the
  already-merged host change.
- GitHub Actions for `bad2b40527c7d21c6647b692e5ab2f2bffa1c06f` completed:
  full CI, PyPy 3.11 container smoke, experimental PyPy 3.12 beta, and slot /
  runtime benchmark workflows all succeeded.
- The separate provider-free full CI run in `.test/mcp2env` completed against
  the pushed `bad2b40` code: `969 passed, 9 skipped, 130 deselected, 29
  warnings` in 1216.52 seconds. No test failures were reported.
- Runtime inspection shows the pinned AMD source remains unchanged and its
  durable session (`e0026aa5-ede0-5608-a8cd-f87bfa63c72e`) is still expanding
  with one unconsumed frontier item, zero committed parser calls, and no active
  ParseView or parsed graph readiness. Two consecutive attempts reached the
  parser's later stages but then failed with `TimeoutError` because the session
  wall-time limit is 900 seconds. The third attempt is currently live.
- Bonsai served successful completion responses, but they do not prove parser
  output persistence: the worker checks elapsed time after `parse_source`
  returns and raises before graph translation/persistence if 900 seconds has
  elapsed. This repeats expensive work and leaves the durable frontier pending.
  The current session limits are immutable and must not be edited directly in
  the database. No additional source documents were added; graph quality,
  crosslink quality, and follow-up/background maintenance success remain
  unverified.
- After the third attempt failed and immediately reclaimed the same job, the
  maintenance container was stopped to prevent another wasted model cycle.
  Compose could not resolve the currently missing `POSTGRES_PASSWORD` in this
  shell, so Docker was asked to stop only `llm-wiki-maintenance-1`; it exited
  137 after the 30-second graceful window. PostgreSQL, MCP, and Grafana remain
  healthy/running, and no volumes were removed. The parser had not returned to
  graph translation/persistence before termination; verify the source and
  ParseView state again before resuming. The maintenance worker is currently
  stopped and needs a rebuilt image plus a safely configured restart.
- A local implementation now allows an explicitly named
  `parse_limits.parser_profile` to create a distinct durable session/generation
  for the unchanged source revision; request identity includes validated parse
  limits, model, and parser lane so the retry receives a distinct job/session.
  Explicit wall time is capped at 3,600 seconds. The current 2,400-second
  example is within that hard bound. The final focused ingestion + durable
  expansion regression suite passed (`19 passed`); the specifically named
  same-revision retry test also passed, as did changed-file Ruff and
  `git diff --check`. This code has not yet been deployed to the live stack or
  pushed, so it is not an operational recovery yet.
- The third live attempt ended at 23:42:57 UTC with the same post-parse
  `TimeoutError`. The immutable source revision/digest remain unchanged; no
  parse generation commit or ParseView activation occurred. Do not report the
  parsing or crosslink quality requirement as met.

### Active Parse-Session Fencing (2026-10-01 00:42 UTC)

- Fixed the retry-path assumption that queued work could be cancelled through
  Kogwistar's `mark_failed`: that operation only transitions claimed (`DOING`)
  jobs. Pending jobs remain auditable in the queue; a CAS active parse-session
  pointer now makes an old claimed job stale before parser work. Whole-source
  retries share one scope, while targeted reparses use region-specific scopes.
- Added worker-path coverage showing a pending expansion job for the 900-second
  profile is rejected as `parse_session_superseded` after the 2,400-second
  profile becomes active. The source revision and revision document remain
  unchanged. Focused durable expansion/session/guard/ingest tests pass:
  `33 passed` (and the retry-specific group: `15 passed`).
- The first full local run (`973 passed`) was discarded as evidence because the
  shell's inherited `PYTHONPATH` loaded Kogwistar from the unrelated root
  checkout. The authoritative rerun explicitly placed this worktree's pinned
  Kogwistar, parser, sink, and `src` paths first and passed:
  `973 passed, 9 skipped, 130 deselected, 29 warnings` in 722.60 seconds.
  CI lint selection `ruff check src tests scripts --select E4,E7,E9,F` and
  `git diff --check` also pass.
- The maintenance container remains stopped; no additional ingestion or
  quality evaluation has occurred. The existing stock source remains the only
  authorized experiment input, and parsing/crosslink quality is still
  unverified.
- Region-scope hardening follow-up: the active session key includes the
  derivation scope, so distinct targeted regions do not supersede each other.
  The 15 focused tests and CI-selected Ruff pass after this adjustment.

### Final CI And Monitoring Cutoff (2026-10-01 01:05 UTC)

- Latest code commit `ea40213` passed all four GitHub workflows: CI (including
  CPython 3.12/3.13/3.14, PyPy 3.11, and Kogwistar Rust checks), PyPy 3.11
  container smoke, experimental PyPy 3.12 beta, and slot/runtime benchmarks.
  The full local CI slice was run against the explicitly selected worktree
  imports and passed `973 passed, 9 skipped, 130 deselected` before the final
  region-scope refinement; the refinement itself passed 15 focused tests and
  is covered by the green full GitHub matrix.
- Rebuilt the local `profchan/kogwistar-llm-wiki:v0.5.1` maintenance image
  after the region-scope change; image ID is
  `sha256:ea45498f553f0ba758eb0c8d6ffa8a4b6a11c047a8ef280b4d059da9b8a586ed`.
  This is a local build only, not a published image.
- The 10-hour experiment monitoring cutoff was enforced at 00:48 UTC.
  At cutoff, `llm-wiki-maintenance-1` was stopped (`Exited 137`); Postgres,
  MCP, and Grafana remained available. No further worker/session monitoring or
  new ingestion was performed after the cutoff. Parsing, ParseView activation,
  crosslink quality, and follow-up/background maintenance therefore remain
  unverified. Do not claim the Bonsai experiment succeeded.

### Post-Cutoff Verification (2026-10-01)

- Without restarting or inspecting the live maintenance stack, verified that
  all four GitHub workflows for commit `466f5f3229e70d9a224df90144ff300f705a5138`
  completed successfully: the standard CI matrix, PyPy 3.11 container smoke,
  experimental PyPy 3.12 beta, and slot/runtime benchmarks.
- Re-ran the relevant local durable-expansion, parse-session, and configured
  parser-provider regression suites: `18 passed` in 26.70 seconds.
- Ran the direct/background maintenance, observation critic, one-follow-up,
  scheduler, and worker-orchestration suites with `PYTHONPATH` explicitly
  pointing at this worktree's vendored Kogwistar, parser, sink, and `src`;
  import-path output confirmed the intended Kogwistar and app. Result:
  `93 passed, 4 warnings` in 102.29 seconds.
- These checks validate code and CI only; they do not change the experiment
  outcome. No Bonsai parse generation, active ParseView, crosslink quality, or
  follow-up/background maintenance success has been verified.

### CPU Finance-Text Embedding Smoke (2026-10-01)

- Added an opt-in BGE-small English text encoder profile alongside (not in
  place of) Qwen3-VL and CLIP. The exact Hugging Face revision is
  `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`; `model.safetensors` is
  133,466,304 bytes with SHA-256
  `3c9f31665447c8911517620762200d2245a2518d6e7208acc78cd9db317e21ad`.
- Built the standalone CPU embedding image with the pinned Torch CPU runtime,
  started only `embedding-bge-cpu` on loopback port 8793, and confirmed its
  readiness profile: dimension 384, dot metric, 512 token limit, profile
  fingerprint `5728cd9a3904c8e5eb6d1682ff251d0022aeb4aba5df0756da3301226f8748d7`.
- A one-query/two-document smoke returned 384-D normalized vectors. For query
  “GPU accelerators for AI training in data centers,” cosine-equivalent dot
  scores were `0.8135` for an AMD Instinct MI300X description and `0.4343` for
  an unrelated coffee-machine passage. First query latency was 1,619.6 ms;
  the subsequent two-document batch took 187.3 ms. This is a single local
  functional smoke, not a stable performance or finance retrieval benchmark.
- The test service was stopped and its Compose network removed. No request was
  sent to the LLM-Wiki graph or database, and no existing vector profile or
  dimension was changed. BGE is text-only; CLIP remains the optional shared
  text/image space. CPU retrieval quality on the actual finance corpus remains
  to be evaluated after the Bonsai parsing/crosslink quality gate succeeds.

### BGE Change CI Completion (2026-10-01)

- Commit `3696a6d` (`feat: add isolated BGE CPU embedding profile`) completed
  all four GitHub workflow runs successfully: `36803810016`, `36803809998`,
  `36803809982`, and `36803809959`.
- The previously queued run `36803809998` is now terminal-success. The branch
  worktree is clean at this verification point.
- This is code/CI verification only. BGE remains an isolated CPU embedding
  service and has not been connected to the finance graph. The Bonsai source
  parsing, active ParseView, graph cross-links, and background/follow-up
  maintenance outcomes remain unverified; the capped maintenance runtime was
  not restarted.

### Durable Parse Wall-Time Overrun Handling (2026-10-01)

- Fixed a loss path in durable expansion: a parser result returned successfully
  after `wall_time_seconds` previously raised `TimeoutError` before translation
  and persistence. That discarded the completed model work and left the same
  frontier available for another expensive attempt.
- A late result is now translated and persisted as revision-pinned generation
  evidence, with elapsed/budget values attached to member diagnostics. The
  session transitions to `review_required`, the current active ParseView is
  not switched, readiness is not declared, and the maintenance job is
  acknowledged rather than requeued. A new explicit parser profile/review is
  required to continue; the wall-time setting remains an overrun detector, not
  a mechanism that can interrupt an already-running synchronous model call.
- Stable parse reconciliation that requires review now stops before advancing
  the maintenance plan, so crosslink follow-up is not launched on a parse view
  that was not accepted.
- Tests pass on the updated code: durable expansion `8 passed`; worker
  orchestration plus maintenance observation `58 passed, 4 warnings`; Ruff,
  `git diff --check`, and the `.test/` ignore check pass.
- This is not a live Bonsai result. The capped runtime was not restarted, and
  no claim is made that a ParseView activated or that finance parsing/crosslink
  quality passed review.

### Local Image For Late-Parse Recovery (2026-10-01)

- Built `kogwistar-llm-wiki:bonsai-late-parse-6aeef39` from the feature
  worktree at commit `6aeef39`; image ID is
  `sha256:fe5978fd1b502097b34d9648f9ce623d5577780754f4acf4bfd091f46b9e3380`
  and compressed/local image size is 815,783,198 bytes.
- A one-shot `docker run --rm --network none` imported the packaged maintenance
  worker and verified the overrun persistence/review-fence implementation is
  present. The container had no network, database mount, or service entrypoint.
- This image has not been started as the maintenance service. No database or
  model was accessed; a fresh, bounded runtime validation is still required to
  prove that Bonsai parsing persists and passes finance-source quality review.

### Provider-Neutral Regression Rerun (2026-10-01)

- Re-ran the durable parse expansion, worker runtime orchestration,
  maintenance observation, observation persistence, and observation critic
  suites on the current feature commit using `-p no:cacheprovider`.
- Result: `89 passed, 4 warnings` in 144.78 seconds. The warnings are the
  existing reserved workflow-state-key warnings in Kogwistar runtime tests.
- This exercises the common configured parser/maintenance paths; it does not
  establish Bonsai model quality or a live graph-maintenance outcome. No local
  model, database, Compose service, or capped session was started or inspected.

### Local Bonsai Artifact And CLI Check (2026-10-01)

- Confirmed the custom executable exists at
  `D:\prism-llama.cpp\build\bin\Release\llama-server.exe`; the Bonsai GGUF
  and mmproj files both exist under `D:\models\bonsai2`.
- Ran only `llama-server.exe --help` (exit 0), without loading weights or
  starting a listener. The help output recognizes every flag used by
  `scripts/start_local_bonsai.ps1`: model/mmproj, context, GPU layers,
  parallelism, K/V cache type, flash attention, fit target, reasoning settings,
  host/port, and log verbosity.
- This establishes that the installed custom binary and launcher argument
  surface agree. It does not verify model loading, available context, GPU fit,
  inference quality, or maintenance connectivity; no capped runtime/session
  was started or inspected.

### Feature-Branch CI Follow-Up (2026-10-01)

- GitHub Actions run `36811311353` for the pushed feature-branch head
  `b469a60be5f592b119e9e6b858bf06dc0fa51f47` completed successfully. The run
  summary lists Python lint, Rust checks, resource comparison, and the normal
  provider-free Python suites for PyPy 3.11 and CPython 3.12, 3.13, and 3.14.
  This is hosted CI evidence for that exact commit; it does not verify the
  stopped local Bonsai runtime or any live graph-maintenance outcome.
- The same-host benchmark run `36811311312` and PyPy 3.11 container smoke run
  `36811311359` completed successfully.
- Optional PyPy 3.12 beta run `36811311341` has a non-blocking install-step
  failure for the bounded NumPy-free source profile. Its evidence artifact
  `pypy312-profile-evidence` (artifact ID `11138869928`, 10.7 KB) was uploaded.
  The exact pip failure remains undiagnosed: the unauthenticated GitHub API was
  rate-limited and the public UI download route did not yield the artifact.
  The workflow's `continue-on-error` policy means its overall success status is
  not evidence that this install step passed. This optional beta failure does
  not invalidate the successful stable PyPy 3.11 and CPython matrix.
- No model server, maintenance worker, container, or database was started or
  inspected during this follow-up. The previously capped local runtime remains
  outside this verification window; a fresh authorized runtime window is still
  required for the outstanding direct/follow-up/background Bonsai checks.

### BGE-Small CPU Encoder Smoke (2026-10-01)

- Ran the existing `llm-wiki-embedding-bge-cpu:local` image with
  `--network none`, a read-only bind mount of the downloaded model, one CPU,
  and a 2 GiB memory limit. No Compose service, graph, vector store, or Bonsai
  process was accessed.
- The encoder loaded `BAAI/bge-small-en-v1.5` revision
  `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` with Torch `2.8.0+cpu` and
  returned normalized 384-D vectors using CPU. Cold load took 16.291 seconds;
  five document encodings took 4.667 seconds; five query encodings took 0.215
  seconds total. Process peak RSS was 459.8 MiB under the container limits.
- On five deliberately small finance-topic query/document pairs, expected
  documents ranked first for all five (MRR 1.0). This is only a smoke sanity
  check, not a representative finance retrieval benchmark or proof of useful
  graph search quality. BGE remains a separate 384-D profile and was not
  connected to the existing graph; the active vector schema/profile is not
  migrated by this check.
- A direct host-Python attempt was rejected before model load because the
  encoder contract requires Torch 2.8.0 while the host environment has
  `2.14.0+cpu`. The isolated pinned CPU image is the verified execution path;
  do not weaken the runtime-version guard based on that host mismatch.
