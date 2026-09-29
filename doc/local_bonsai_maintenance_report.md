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
  The maintenance container was recreated from this local image and is healthy;
  it now has a 16,000-token per-job background budget and a 4,096-token
  reserved output cap per critic call.
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
  exposed the separate provider-construction cap:
  `KOGWISTAR_MAINTENANCE_MAX_OUTPUT_TOKENS=1024` was overriding the per-call
  setting. At that point, the local `.env`, Compose fallback, and redacted
  config backup were raised to 2,048. They are now at 4,096. Focused tests
  after the diagnostic change passed (`40 passed`),
  Ruff passed, and Compose configuration validation passed.
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

Model-size comparison sources: Apple's [MobileCLIP repository](https://github.com/apple-aiml-research/ml-mobileclip)
describes the image/text model family and inference stack; the [MobileCLIP-S0
checkpoint page](https://huggingface.co/apple/MobileCLIP-S0) reports a 216 MB
checkpoint. This alternative is recorded for evaluation, not adopted.
