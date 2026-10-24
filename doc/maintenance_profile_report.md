# Maintenance And Inbox End-to-End Profile

**Run date:** 2026-10-24

**Status:** Interim evidence; production-scale matrix is not complete.

**Embedding benchmark qualification:** the historical deterministic cross-link
replay and most listed real E5 profiles explicitly ran
`intfloat/multilingual-e5-small` on CPU. Those timings do **not** establish the
latency or bottleneck of the intended GPU-backed deployment. They are CPU
diagnostic measurements only.
The cross-link profiler now defaults to `--embedding-device cuda` and fails
closed if CUDA is unavailable; CPU requires the explicit `--embedding-device
cpu` option. The PostgreSQL namespace builder now exposes an opt-in
`embedding_cache_dir`, which maps to isolated Kogwistar persistent cache paths
per graph space. The profile records cache lookups, hits, and misses by
lifecycle phase. Its E5 cache identity includes the exact locally downloaded
model snapshot and the pooling/truncation implementation version. Model-weight
download caching is separate from embedding-result caching.

**GPU encoder smoke (2026-10-24):** on the RTX 3080 Laptop GPU, the local
`intfloat/multilingual-e5-small` loaded on CUDA and encoded two smoke strings
into normalized 384-dimensional vectors. Model load was 31.0 s and the two-text
call was 1.384 s. This confirmed the GPU execution path, but was not an
end-to-end replay or throughput benchmark.

**GPU result-cache replay (2026-10-24):** the deterministic known-answer
cross-link lifecycle was then run with CUDA E5 and PostgreSQL/pgvector, once
without result caching and twice with the same persistent cache directory
(cold, then warm). All three runs passed the lifecycle gates: one accepted edge,
640/640 index jobs DONE, and semantic self-retrieval. The cache-off run encoded
3,502 items in 132.52 s (28.00 s model load); the cache-cold run encoded 3,450
items in 127.81 s (31.07 s load), with 13/890 cache lookups hitting; the
cache-warm run encoded 3,340 items in 119.48 s (31.25 s load), with 50/890
lookups hitting (5.6%). Warm-run cache hits were concentrated in conversation
and graph artifacts; generated lifecycle text remains mostly unique. Whole
measured lifecycle phases totaled about 310.95 s without cache, 328.54 s cold,
and 298.17 s warm. Because these are single runs with slightly different item
counts and low hit rates, they do not establish a reliable end-to-end speedup.
They do establish that caching is available and correct, while this workload is
not sufficiently repetitive to show a large cache benefit. Stable repeated
documents or queries should produce more reuse than generated maintenance
conversation artifacts.

**Deterministic maintenance workflow benchmark (2026-10-24):** three fresh-
schema samples ran the existing maintenance E2E harness with a deterministic
structured critic payload and deterministic 2D embeddings. This intentionally
does not call Bonsai or any other LLM and isolates the application, graph,
queue, and PostgreSQL/pgvector work. PostgreSQL was in a disposable container
capped at 2 CPU/4 GiB; the Python process ran locally. Each sample processed 3
jobs, completed 3/3 sensible `adequate` assessments, verified source spans,
finished all 48/48 conversation index jobs, and passed semantic self-retrieval.
Each issued 1,115 SQL statements and made 258 deterministic embedding calls
(about 5 ms total). Worker-drain times were 4.34, 4.81, and 5.03 seconds
(median 4.81 s); engine startup plus seed/enqueue plus materialization was
about 4.0-4.9 seconds, for 8.4-9.9 seconds per complete run. This is the
intended quick workflow benchmark, not live-model latency or a production-scale
capacity claim. Bonsai's role remains a separate one-call-scale capability
smoke, not a timed workflow benchmark.

**Controlled repeated-input cache comparison (2026-10-24):**
[`benchmark_embedding_result_cache.py`](../scripts/benchmark_embedding_result_cache.py)
uses the same `GraphKnowledgeEngine` defensive embedding API for both cached
and uncached paths, with 24 fixed synthetic passages, three warm-up texts, and
five passes per sample. Model loading is reported separately and excluded from
the timed paths. Two matched CUDA samples produced:

| Sample | Uncached, 5 passes | Cache cold pass | Four cache-warm passes | Warm hits | Cache vs uncached |
|---:|---:|---:|---:|---:|---:|
| 1 | 9.351 s | 2.094 s | 0.891 s | 96/96 | 68.1% lower elapsed time |
| 2 | 9.692 s | 2.334 s | 0.904 s | 96/96 | 66.6% lower elapsed time |

The uncached figures are 120 logical engine calls; the cached figures are 24
cold misses plus 96 warm hits. Both samples had 0.0 maximum absolute vector
difference between cached and uncached results. E5 model load was 25.03 s and
24.22 s, respectively, and is not part of those comparisons. The first of four
warm passes took about 0.62 s in each sample; the next three took about
0.084-0.102 s each, indicating one-time local cache-read overhead followed by
very cheap repeated reads. The cache-cold pass alone was slower than the
uncached median per-item path, so caching is advantageous when inputs repeat,
not for first-time unique text. This is a controlled two-sample microbenchmark
on synthetic short passages, not a production-corpus or end-to-end throughput
claim. It complements the maintenance replay, where generated text had only a
5.6% warm hit rate and no reliable whole-run speedup was established.

**GPU replay and index-namespace audit (2026-10-24):** one further fresh-schema
fixture replay used CUDA E5 with result caching deliberately disabled, ACLs
enabled, and PostgreSQL capped at 2 CPU/4 GiB. It passed the known-answer
relation, ACL audit, edge persistence, terminal indexing, and semantic
self-retrieval gates. E5 loaded in 96.05 s and encoded 3,502 items in 129.28 s
(p50 35.71 ms, p95 48.92 ms per invocation); 2,719 items / 99.75 s occurred
during proposal and review, and 595 / 21.59 s during apply. Measured workflow
phases summed to 291.70 s (proposal/review 164.44 s, approval/apply 118.23 s,
reconciliation 0.013 s, self-retrieval 9.02 s). This is a cache-off GPU replay,
not a fully containerized application benchmark: only PostgreSQL was resource
capped, while Python and the GPU ran on the host.

The audit found 640 terminal index jobs, representing 255 node IDs and 65 edge
IDs with no duplicate projection-job rows. Canonical entity events for all
those IDs were found in the conversation engine's base `conversation`
namespace; the workspace-scoped namespace had none. Thus the earlier
workspace-only `no_event` result was an audit-namespace mismatch, not proof of
missing canonical events. An earlier lookup through `engine.read` returned only
8 of 255 node IDs and no edges, despite active events for all IDs. That result
was initially misclassified as missing vector rows. The engine was ACL-enabled,
and `engine.read.get_nodes/get_edges` applies ACL visibility filtering. A later
fresh-schema run used an explicitly labeled raw backend census and found all
240 node IDs and all 65 edge IDs physically present, matching their active
events. The old 8/0 result is therefore consistent with ACL filtering, not
evidence of data loss. Semantic self-retrieval passed in both runs.

**Diagnostic overhead measurement (2026-10-24):** a repeat with identical
workload/provider/storage settings passed all the same lifecycle gates. The
profile wall time before cleanup was 528.24 s. Proposal/review, approval/apply,
reconciliation, and semantic self-retrieval accounted for 282.53 s; the eight
explicit audit censuses accounted for another 204.57 s (38.7% of profile wall
time). Individual audit costs ranged from 0.010 s for the empty pre-run queue
to 44.18 s for the post-apply artifact census; the post-proposal queue audit
cost 41.25 s and the final queue/event/entity census 36.09 s. The remaining
41.14 s is setup, fixture seeding, and other uninstrumented checks. Thus the
earlier long wall time was materially inflated by profiling diagnostics; it
must not be presented as production workload latency. The harness now records
job-list, backend-entity-read, per-namespace event-scan, and node/edge artifact
read timings separately for subsequent runs. This repeat loaded CUDA E5 in
27.26 s and encoded 3,502 items in 123.20 s; the prior cache-off run took
96.05 s to load and 129.28 s to encode 3,502 items. These are two samples, not
a stable cold-start estimate.

**Matched read-mode follow-up (2026-10-24):** the same deterministic
known-answer replay was repeated after `ReadSubsystem` began explicitly
selecting backend validation mode for returned Nodes and Edges. It again passed
the relation, ACL, persistence, terminal-indexing, and semantic self-retrieval
gates. CUDA E5 loaded in 27.59 s and encoded 3,502 items in 125.21 s. Workflow
phase time was 263.02 s (proposal/review 155.49 s, approval/apply 100.42 s,
reconciliation 0.011 s, retrieval 7.10 s). The eight diagnostic audits took
149.19 s, down 55.38 s (27.1%) from the preceding instrumented run; total wall
before cleanup fell from 528.24 s to 453.29 s (14.2%). This is one matched
before/after pair, so it supports the optimization but is not a repeatable
production speedup estimate. The provider was deterministic fixture code; no
LLM inference was timed. Python and CUDA ran on the host; only PostgreSQL had a
2 CPU/4 GiB container limit.

**Metadata-only census replay (2026-10-24):** a fresh loopback-only PostgreSQL
17/pgvector container capped at 2 CPU/4 GiB was used for another cache-off,
ACL-enabled CUDA E5 known-answer run. It passed expected relation direction,
ACL parity, both source spans, one persisted edge, 610/610 terminal index jobs,
and semantic self-retrieval. The deterministic fixture provider made no LLM
inference calls. Python and GPU inference ran on the host; the database alone
was container-limited. E5 loaded in 27.63 s and encoded 3,476 items in 122.59 s
(p50 34.30 ms, p95 45.75 ms per invocation). Workflow phases were 154.22 s
proposal/review, 93.37 s approval/revalidation/apply, 0.010 s reconciliation,
and 18.02 s semantic self-retrieval. Total profile wall before cleanup was
307.66 s.

The profiler census now calls the backend for IDs and metadata only; it does
not hydrate full models or vectors and intentionally bypasses request-level ACL
filtering to check physical persistence. It emits only aggregate counts, never
entity IDs or ACL values, and is used only against the synthetic disposable
schema. All 240 of 240 indexed node IDs and all 65 of 65 edge IDs had backend
rows and active canonical events in the base `conversation` namespace. No
duplicate projection-job keys were found. The eight diagnostic censuses took
0.758 s total, versus 149.19 s in the preceding instrumented run (which used
ACL-filtered `engine.read` calls and full model hydration for its census). The
pre-cleanup wall time fell by 145.62 s, from 453.29 s to 307.66 s. This
comparison changes the census method and has one sample per method; it shows
that the former integrity audit was expensive and ACL-filtered, not that normal
application traffic became faster. The earlier 8/0 read result is superseded
as a storage-integrity finding; it was consistent with ACL visibility
filtering. The audit suite passes 25 tests and Ruff passes.

Reproduce with locally cached model weights and a CUDA-enabled environment:

```powershell
python scripts/benchmark_embedding_result_cache.py --device cuda --unique-items 24 --repeat-rounds 5 --warmup-items 3
```

The cache was also hardened after a first probe revealed that joblib's default
logging/argument metadata could expose plaintext embedding inputs. The
Kogwistar cache now uses a SHA-256-derived key, passes the query transiently
only on misses, and disables verbose joblib output. A focused regression test
checks that secret input text is absent from cache metadata and output. This
protects cache artifacts; it does not replace the application's normal data
handling and retention controls.

**Repeated live GPU-E5 profile (2026-10-24):** two independent fresh-schema
runs used the real Ternary Bonsai2 `PTQ1_0` model through Prism build 10706,
with no vision projector, 4,096 context, Q4 KV cache, and all 65/65 layers
offloaded to the RTX 3080 Laptop GPU. The local
`intfloat/multilingual-e5-small` embedder ran on CUDA (384 dimensions, maximum
sequence length 512). Both runs used ACL-enabled PostgreSQL/pgvector in a fresh
loopback-only container capped at 2 CPU/4 GiB, and shared one new persistent
embedding-result cache directory. Each run independently passed proposal,
deterministic source/revision/span validation, Bonsai critic approval,
human-pending review persistence, synthetic reviewer approval, revalidation,
edge application, ACL parity, 610/610 terminal index jobs, and semantic
self-retrieval. Both persisted the expected `uses` relation and both exact
source spans. Each run made one live proposal call and one live critic call.

| Fresh-schema sample | E5 load | CUDA E5 items / measured time | Cache hits / lookups | Profile wall before cleanup | Proposal/review | Approval/apply | Self-retrieval |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1, cache directory initially cold | 31.72 s | 3,424 / 143.91 s | 13 / 884 (1.5%) | 444.34 s | 267.37 s | 107.53 s | 20.43 s |
| 2, same persistent cache directory | 7.65 s | 3,314 / 146.36 s | 50 / 884 (5.7%) | 422.25 s | 267.17 s | 108.41 s | 19.95 s |

The second run reused more embedding results, but its measured E5 compute time
was not lower; these two samples do not demonstrate an end-to-end cache speedup.
Most generated maintenance/conversation inputs were unique, so persistent
result caching remains most useful for stable repeated documents or queries.
The model server generated at about 33 tokens/s during proposal and briefly
reached about 90% GPU utilization; the server log confirmed 65/65 layers on
CUDA. Python, Prism, and the GPU were host processes with no CPU/RAM quota;
only PostgreSQL was container-limited. The fixture and final reviewer decision
are synthetic, so the repeated passes establish a bounded real-provider
protocol/lifecycle path, not representative-corpus semantic quality or
production-capacity behavior. Generated profile JSON remains in `%TEMP%` and
is not checked in.

**Live local-model smoke versus workflow benchmark (2026-10-24):** Bonsai is
used here only as a bounded smoke test that the local model can perform a
meaningful maintenance task through the real provider, validation, persistence,
and indexing path. It is not the provider used to benchmark workflow throughput.
The timed workflow benchmark should use known-good deterministic/cached
provider results and precomputed or appropriately cached embeddings, so its
measurements isolate application, graph, queue, and storage work. Embedding
result caching does not cache LLM generation.

Two exploratory three-job Bonsai maintenance-observation runs confirm why these
must remain separate. At a 1,024-token output cap, all three jobs were
acknowledged and all 48 conversation index jobs completed, but none of the
critic responses produced a valid assessment; queue drain was 158.21 s. At a
2,048-token cap, only one of three produced a successful assessment; the other
two failed the output/quality gate, and queue drain was 329.26 s. Both runs
therefore failed the end-to-end correctness gate despite completed queues and
indexing. CUDA E5 encoded 258 items in 12.46 s in the first run, while the
sequential Bonsai calls dominated wall time. These are diagnostic smoke
results, not workflow benchmark samples, and they do not indicate that the
deterministic benchmark needs to take minutes. The 2,048-token retry was an
unnecessary expansion of the smoke matrix for that purpose; use one bounded
live smoke, then stop, and keep timing comparisons on deterministic/replay
paths.

**Prior single-sample live profile (2026-10-24):** Prism `llama-server.exe`
build `10706` / commit `1a07bfa5f` loaded the D-drive Ternary Bonsai2
`PTQ1_0` GGUF without its vision projector, at 4,096 context with Q4 KV cache.
One ACL-enabled end-to-end profile passed on a fresh loopback-only PostgreSQL/
pgvector container capped at 2 CPU/4 GiB. The model proposed the expected
`uses` relation; deterministic validation and the model critic passed; a
human-pending review artifact was persisted; the harness then approved it
synthetically and revalidated/applied the edge. Endpoint, edge, grounding, and
both source-span ACL policies matched. All 610 indexing jobs were DONE, with no
pending/doing jobs, and semantic self-retrieval passed. Proposal/review took
504.5 s; the post-review approval/apply/index/retrieval path took 186.0 s. This
is a successful real-model + PostgreSQL ACL protocol check on a synthetic
fixture, not representative-corpus quality or production-capacity evidence.

The first readiness probe at 2,048 total context became healthy, but was too
small for the 2,048-token completion cap plus the provider prompt and ended in
`LengthFinishReasonError` after 218.1 s. It created no pending group and applied
no edge. Restarting at the harness-matched 4,096 context became healthy and the
full profile passed. Thus the earlier 480-second no-listener observation was
not reproduced; it remains a historical failed attempt, not the current status.
The fresh profile container and only the Prism process started for these tests
were stopped/removed afterward; the pre-existing database container was left
untouched. The separate user-reported 32K/Q8, vision-off Bonsai configuration
was not measured by this 4K/Q4 profile and should not be conflated with it.

**Latest validation:** after the two-sided-span serializer, failure-recording,
and configurable completion-cap changes, the cross-link proposal, profile
audit, and maintenance patch suite passed 59 tests together. Ruff and Python
compilation passed for the profiler and focused test files.
Three warm-server runs of the corrected four-case Bonsai2/Prism critic set
passed 12/12 source-grounded verdict/evidence checks; this is synthetic quality
evidence, not a full maintenance workload run. The repository `.env` cannot
authenticate to the existing disposable PostgreSQL container. The latest
cross-link run avoided that mismatch using a separate, capped, throwaway
database. Three fresh-schema
real-model E2E samples of 3 jobs each and one 10-job sample completed with all
jobs, critics, indexing, and semantic self-retrieval gates passing. These are
synthetic workload results without container resource limits, not production
capacity evidence. The full core lane-message test file previously reported 6
passed, 2 skipped, and 4 failed in 297 s. The focused suite covers Prism
configuration, output limits, redacted assessment summaries, synchronous
visibility probes, and critic-evaluation fixture invariants.
The real-provider cross-link profiler has completed an earlier live synthetic
Bonsai2/Prism run and a newer output-cap comparison; see the focused result
below. Its
success gate now checks the observed relation, exact endpoint set, and exact
two fixture spans without retaining excerpt text. The gate now also compares
the persisted ACL policy on both endpoint nodes with the new edge and each of
its grounding/span records, emitting only a boolean audit result. The gate is
unit-tested and designed to catch policy drift without exposing ACL values in
traces or to the model; it was exercised by the 2026-10-24 live ACL profile
recorded above. The PostgreSQL namespace builder now exposes an opt-in `acl_enabled`
setting and passes it to each core graph-engine constructor; the profiler has
an explicit `--acl-enabled` switch and the synthetic graph entities declare
public visibility. The default remains `False`, preserving existing behavior.
Builder forwarding is unit-tested, and a separate ACL-enabled in-memory
integration test verifies that both spans and matching
endpoint/edge/grounding/span ACL records persist. A live ACL-enabled PostgreSQL
backend integration test now also verifies builder construction, cross-link
application, exact source spans, and matching endpoint/edge/grounding/span ACL
records. Applying it exposed that derived edges were inheriting the worker's
default ACL rather than the endpoints' ACL. Cross-link application now copies
an identical endpoint policy and rejects missing or incompatible endpoint ACLs
because the current ACL model cannot represent general policy intersections.
The live model-backed profile with `--acl-enabled` has now completed once at
4K context: proposal, critic, human-pending persistence, approval, revalidation,
ACL-preserving edge/span persistence, indexing, and semantic self-retrieval all
passed. This closes the first end-to-end ACL profile gate, but repeatability and
representative-corpus quality remain unverified.
The follow-up builder/profile configuration tests passed (29 tests), as did the
focused ACL propagation integration test (1 test). After endpoint-policy
inheritance changes, 15 targeted unit/profile tests passed and the live
PostgreSQL ACL integration test passed. Ruff passed for the changed builder,
profiler, and tests; the maintenance apply module still has two pre-existing
BLE001 lint findings outside the new code.
The known-answer fixture replaces the earlier ambiguous CUDA prose with the
fact “Training workload Alpha uses CUDA,” and audits direction plus the exact
`uses` predicate. The earlier live sample did not test this expectation; the
newer 2,048-token-cap 3/3 repeat run did and all three matched. This verifies
the constrained fixture only, not open-ended cross-link quality.
A read-only authentication check confirmed the repository `.env` does not
authenticate to the existing disposable PostgreSQL container. No credentials
were inspected or changed. The follow-up profiles used an independent
throwaway container with random in-memory credentials and removed it afterward.
Streamed profile traces and saved
profile JSON now retain
only allowlisted assessment verdict/action, finding count, and severity counts;
finding messages, evidence IDs, and model-generated finding codes remain
excluded. All four lane-message integration failures are Chroma-backed
cases that reject a nested `atomic_retained_child` metadata mapping; changing
the embedding-function parameter name did not resolve this storage-adapter
failure. A local-venv, one-entry PostgreSQL maintenance smoke completed with
all correctness checks passing and streamed nine bounded trace events. A prior
1,000-item maintenance-profile attempt exceeded 15 minutes and was stopped
incomplete. It used the synthetic-provider profile path; it was **not** a
1,000-real-LLM-request benchmark. A 150-item run also exceeded 15 minutes and
was stopped after 88 distinct jobs had persisted assessments; this is partial
progress, not a completed benchmark sample. The largest completed real-model
maintenance profile currently recorded is 10 jobs (10 critic calls), and the
bounded cross-link experiment is 3 independent one-job samples (each proposal
plus critic). These sample sizes are evidence about functionality and latency,
not a large-scale LLM throughput benchmark.
The PostgreSQL canonical incident-edge query now passes an integration test
covering projection staleness, ordinary and single-endpoint edges, edge-linked
hyperedges, transaction read-your-own-write, and rollback. A fresh 30-job run
completed on PostgreSQL after that correction; it remains a synthetic-provider
profile, not a production-model performance result. A later one-job run used
the cached local multilingual E5 model and local Bonsai critic: storage and
source-evidence checks succeeded, but the critic output failed structured
parsing. The queue marked the job done, while the harness correctly marked the
end-to-end result unsuccessful because no critic assessment succeeded.
The Ollama `num_ctx` provider mapping and profile context-cap tests also pass.
The earlier Ollama Bonsai-tag 2K- and 4K-context profiles completed their
one-job traces but did not produce valid structured assessments. A separate run
using the actual Ternary Bonsai 2 model through the local Prism `llama.cpp`
build completed the maintenance critic successfully with a 1,024-token output
cap. Its 256-token control hit the completion limit; it did not demonstrate a
context-window failure. The profile now distinguishes output-token exhaustion
from context overflow and verifies conversation index-job completion plus
semantic self-retrieval.

## Scope

This profile follows the reviewer recommendation to measure complete persistence
paths before considering broader runtime optimization. It uses the current
LLM-Wiki and vendored Kogwistar code, disposable PostgreSQL 17 with pgvector,
fresh schema per run, deterministic 2D embeddings, and a fake structured critic
for synthetic rows. The explicitly labeled local-model row exercises local
embedding inference and a local LLM; it is one diagnostic smoke, not a
performance benchmark. OTel-enabled runs use an in-process counting exporter,
not Grafana or an OTLP network endpoint.

The harness is [`profile_maintenance_e2e.py`](../scripts/profile_maintenance_e2e.py).
It profiles maintenance jobs and the authoritative lane-message graph-plus-
projection path separately. Generated database contents and raw result JSON
remain outside source control.

## Interim Results

Each row below is one fresh-schema sample, not a statistically stable benchmark.
Times include application graph persistence, not container startup. The 100-item
lane run was single-stage with four claim workers.

| Path | Persistence mode | Items | Correctness | Key latency / rate | SQL statements |
|---|---:|---:|---|---|---:|
| Maintenance observation job | single-stage | 1 | pass; source span verified | queue drain 2.05 s; enqueue 14.8 ms | 495 |
| Maintenance observation job | two-stage | 1 | pass; source span verified | queue drain 1.56 s; enqueue 14.3 ms | 355 |
| Local-venv maintenance smoke, live trace | single-stage | 1 | pass; 1 job DONE, critic succeeded, source span verified | worker drain 2.63 s; 9 trace events; 102 synthetic embedding calls in 2.10 ms total | 495 |
| Synthetic control after local-model diagnostics | single-stage | 1 | pass; source span verified, critic succeeded, queue DONE | queue drain 2.98 s; 9 trace events | 459 |
| Maintenance after canonical incident-query fix | single-stage | 30 | all jobs DONE; all 30 critic assessments succeeded; source span verified | drain 39.81 s; dispatch p50 1.28 s, p95 1.48 s; 0.75 jobs/s | 9,913 |
| Local-model maintenance smoke | single-stage | 1 | **end-to-end failed**; job acknowledged, source span verified, 0 successful critic assessments | drain 40.72 s; E5 load 28.12 s; 102 embedding calls / 15.09 s; Bonsai critic 24.10 s then structured-output parse failure | 460 |
| Local Bonsai maintenance smoke, `MichelRosselli/bonsai-27b:Q1_0` | single-stage | 1 | **failed**; job acknowledged, source span verified, 0 valid critic assessments | drain 227.85 s; critic 213.46 s; E5 load 29.33 s; 102 embedding calls / 13.13 s | 466 |
| Local Bonsai, `MichelRosselli/bonsai-27b:Q1_0`, 2K context | single-stage | 1 | **failed**; source span verified, queue DONE, critic parse failure | critic 21.6 s; drain 40.56 s; E5 load 31.67 s; 102 calls / 18.45 s | 460 |
| Local Bonsai, `MichelRosselli/bonsai-27b:Q1_0`, 4K context | single-stage | 1 | **failed**; source span verified, queue DONE, critic parse failure | critic 23.1 s; drain 34.97 s; E5 load 29.71 s; 102 calls / 10.77 s | 460 |
| Ternary Bonsai 2 `PTQ1_0` via Prism `llama.cpp`, 4K context, 256 output cap | single-stage | 1 | **failed**; output reached completion limit; job acknowledged but no critic assessment | critic 20.11 s; drain 32.13 s; E5 load 30.24 s; 102 calls / 10.82 s | 460 |
| Ternary Bonsai 2 `PTQ1_0` via Prism `llama.cpp`, 4K context, 1,024 output cap | single-stage | 1 | **pass**; critic schema/evidence validation succeeded, source span verified, index jobs DONE, semantic self-hit verified | critic 22.51 s; drain 35.30 s; E5 load 32.02 s; 102 calls / 11.48 s | 460 |
| Ternary Bonsai 2 `PTQ1_0` via Prism, repeated warm-service profiles | single-stage | 3 independent one-job samples | all 3 passed; all 3 source spans verified; all 3 index queues terminal and semantic self-hit verified | critic median 21.75 s (21.73-22.21); drain median 33.72 s (33.15-33.76); E5 load median 28.87 s; E5 compute median 10.32 s | 460 each |
| Ternary Bonsai 2 `PTQ1_0` via Prism, sequential multi-job batch | single-stage | 3 | all jobs acknowledged; 3/3 structured critic/evidence validations; 48/48 index jobs DONE; semantic self-hit verified | queue drain 77.75 s; critic p50 15.51 s / p95 17.95 s; E5 load 28.90 s, compute 26.64 s | 1,114 |
| Ternary Bonsai2 + E5 real maintenance E2E, PostgreSQL 17 | single-stage | 3 independent fresh-schema samples, 3 jobs each | 9/9 jobs DONE, 9/9 critics succeeded, source spans verified, 144/144 index jobs DONE, semantic self-retrieval verified; critic verdict counts varied | queue-drain median 75.97 s (75.87-82.10); critic p50 median 15.40 s; E5 load median 28.35 s and compute median 25.54 s / 258 items; RSS delta median 1,001 MiB | 1,116/sample; SQL p95 median 4.06 ms |
| Ternary Bonsai2 + E5 real maintenance E2E, PostgreSQL 17 | single-stage | 1 fresh-schema sample, 10 jobs | 10/10 jobs DONE, 10/10 critics succeeded, source spans verified, 146/146 index jobs DONE, semantic self-retrieval verified; no proposal/apply plans | queue drain 272.25 s (0.0367 jobs/s); critic p50 18.12 s / p95 24.91 s; E5 load 28.40 s, compute 79.43 s / 804 items; RSS delta 1,009 MiB | 3,399; SQL p95 4.00 ms |
| E5 maintenance profile, single-stage persistence, fake critic | single-stage | 1 | maintenance and all 20 conversation index jobs DONE; semantic self-hit verified | drain 11.91 s; 102 embedding items / 10.66 s; final reconcile 13 ms | 462 |
| E5 maintenance profile, two-stage persistence, fake critic | two-stage | 1 | maintenance and all 10 embedding jobs DONE; semantic self-hit verified | drain 7.71 s; 54 embedding items / 6.58 s; final reconcile 12 ms | 546 |
| Corrected E5 lane-send profile, fake critic | single-stage | 3 independent samples, 3 jobs each | all 9 jobs complete; 144/144 index jobs DONE; 9/9 messages semantically retrievable after send returned | send p50 median 8.32 s (8.26-8.74), p95 median 11.11 s; queue-drain median 29.26 s (29.15-30.79); 258 E5 items/sample, median 25.53 s | 1,121/sample |
| Corrected E5 lane-send profile, fake critic | two-stage | 3 independent samples, 3 jobs each | all 9 jobs complete; 72/72 embedding jobs DONE; 9/9 messages semantically retrievable after send returned | send p50 median 5.36 s (5.36-5.52), p95 median 7.08 s; queue-drain median 19.30 s (19.28-19.83); 138 E5 items/sample, median 16.10 s | 1,303/sample |
| Local Qwen text-only control, `qwen2.5:0.5b` | single-stage | 1 | **failed**; source span verified, queue DONE, critic rejected by structured-schema validation | critic 10.87 s; dispatch 25.25 s; E5 load 34.23 s; 102 embedding calls / 13.03 s | n/a |
| Maintenance scale attempt | single-stage | 150 requested, 88 assessed | stopped at 15m35s; incomplete | 5.65 assessed jobs/min; process CPU 823.5 s, RSS 244 MB; not a valid full-run sample | live schema: 1,626 index jobs DONE, 12 DOING, 50 PENDING at cutoff |
| Maintenance + runtime workflow, OTel on, delayed critic | single-stage | 1 + 1 workflow | pass | queue drain 27.54 s, including 2.5 s critic delay and forced 1 s lease renew interval | 2,679 |
| Lane inbox publication and claim/ack | single-stage | 10 | all 11 including warm-up completed exactly once | publish p50 1.41 s, p95 2.05 s; drain 2.40 s | 3,313 |
| Lane inbox publication and claim/ack | two-stage | 10 | all 11 including warm-up completed exactly once | publish p50 1.27 s, p95 1.50 s; drain 3.25 s | 3,099 |
| Lane inbox publication and claim/ack | single-stage | 100 | all 101 including warm-up completed exactly once | publish p50 2.21 s, p95 2.94 s; drain 32.65 s | 29,182 |
| Lane inbox drain, separate processes | single-stage | 10 items, 4 workers | all 11 completed exactly once; pool ready 17.52 s, drain 2.00 s, claim p50 1.05 s | 3,313 |
| Lane inbox drain, separate processes | single-stage | 10 items, 16 workers | all 11 completed exactly once; pool ready 38.55 s, drain 3.54 s, claim p50 2.54 s | 3,469 |
| Observation + runtime workflow | single-stage, OTel off | 1 + 1 workflow | pass; 1 run, 3 steps, 3 checkpoints | queue drain 24.06 s | 2,653 |
| Observation + runtime workflow | single-stage, OTel on | 1 + 1 workflow | pass; 4 spans exported, zero drops/errors | queue drain 24.55 s | 2,653 |

The earlier one-subject comparison suggested two-stage persistence may reduce
work, but it used synthetic embeddings and did not check index backlog or
semantic visibility. It is superseded by the corrected E5 pair below for
embedding-path evidence. Admission enqueue was similar (14.8 ms versus 14.3 ms)
in that synthetic comparison; neither comparison establishes end-to-end
user-facing admission latency.

The later one-entry smoke used the repository `.venv` and the current PostgreSQL
profile container. It streamed worker events while processing: poll start,
claim, job start, strategy start, observation completion, plan completion,
acknowledgment, dispatch completion, and poll completion. The synthetic encoder
was called 102 times for 102 single-item inputs and spent about 2.10 ms total;
worker drain was about 2.63 s. This shows embedding is not the cause of the
measured synthetic-path delay. It says nothing about production embedding-model
latency because no real embedding model or LLM was invoked. A 1,000-item run was
stopped after exceeding the 15-minute practical limit; it is not a valid sample.

### Canonical Incident-Edge Query And Transaction Check

The first indexed query implementation returned no edges in PostgreSQL. The
integration test exposed a storage-format mismatch: Kogwistar persists the
endpoint arrays in edge metadata as JSON-encoded strings, not native JSONB
arrays. The query now casts the stored JSON text back to JSONB and has
field-specific GIN expression indexes. It reads the canonical `gke_edges` rows;
it does not trust the asynchronous `edge_endpoints` projection. The regression
test deliberately deletes that projection and still finds the canonical edge.
The same test runs `EXPLAIN` with sequential scans disabled and confirms the
query can use the source/target expression GIN indexes.

The same test verifies ordinary node incidence, a one-ended edge, an edge-linked
hyperedge, and transaction behavior. A query in the active PostgreSQL unit of
work sees its own newly written edge; raising before commit rolls it back, and
the edge is absent afterwards. At PostgreSQL's default `READ COMMITTED` level,
each query sees committed state as of that statement plus its transaction's own
writes. `REPEATABLE READ` retains the transaction snapshot. Other transactions'
uncommitted writes remain invisible.

The 30-job profile after the correction completed all jobs with verified source
evidence in 39.81 s. It issued 9,913 SQL statements (mean 2.19 ms, p95 3.77 ms,
p99 4.73 ms), used 2,364 deterministic embedding invocations totalling 40.5 ms,
and increased process RSS by about 14.9 MB. This confirms correctness and gives
a bounded workload baseline, but it is a single sample with a fake critic and
deterministic 2D embeddings. It cannot establish production model throughput,
stable scaling, or a measured speedup over the earlier implementation.

### Local Model Smoke: Ollama Results

The one-job diagnostics used the local-only cached
`intfloat/multilingual-e5-small` encoder on CPU (384 dimensions, maximum input
length 512) and local Ollama text-only critics. No vision tower or remote model
was used. The profile now defaults to an explicit 2,048-token Ollama context
(`num_ctx`) and a 256 output-token cap to limit KV-cache use. Earlier Bonsai
and Qwen runs used the Ollama server/model default context; the newer Bonsai
2K/4K runs below explicitly set context.
With `bonsai-parser-8k:latest`, E5 loaded in 28.12 s; 102 embedding calls for
102 strings spent 15.09 s in embedding calls (mean 148 ms, p95 341 ms). The
critic call took 24.10 s and raised `OutputParserException`. With
`MichelRosselli/bonsai-27b:Q1_0`, E5 loaded in 29.33 s and spent 13.13 s across
102 calls (mean 129 ms, p95 286 ms); the critic took 213.46 s and also raised
`OutputParserException`, after seven queue lease renewals. The worker emits a
safe `structured_output_parse_failure` classification for this exception and
does not include raw model output. The profile's trace projection initially
omitted that safe code (showing only the exception class); the projection now
passes the code through, with a regression test that still excludes payload and
source text.

A further text-only control, `qwen2.5:0.5b`, returned in 10.87 s but failed
structured-schema validation. It is a protocol/latency diagnostic only, not a
successful critic or a production-model recommendation. A synthetic control
after these local tests passed end-to-end in 2.98 s, confirming the maintenance
path can complete when the critic fulfills its contract.

The 2K and 4K Bonsai repeats used the same one-item PostgreSQL workload, CPU
E5 embeddings, 2,000-token observation budget, 256-token output cap, and local
text-only `MichelRosselli/bonsai-27b:Q1_0`. Both used the configured context
through Ollama `num_ctx`. Critic latency was 21.6 s at 2K and 23.1 s at 4K;
overall queue drain was 40.56 s and 34.97 s respectively. Both returned the
same safe `structured_output_parse_failure`, with zero successful critic
assessments. This small comparison does not show a meaningful context-related
latency difference; it does show that reducing context did not fix output
conformance. The context cap remains useful for predictable resource usage,
not as a correctness fix.

During the 2K/4K runs, Ollama reported the model allocation as 4.29/4.43 GB in
VRAM, equal to its reported model allocation, while total NVIDIA memory use was
about 5.5/8 GB. No CPU offload was indicated. The subsequent idle snapshot was
not used to infer inference utilization. Both critics tested are text-only;
removing a vision tower would not help. The 2K context is therefore a practical
low-memory setting on this 8 GiB device for this model, though this is a small
sample and not a general guarantee for other model tags or workloads.
After both runs, the Bonsai model was explicitly unloaded with Ollama
`keep_alive=0`; the loaded-model list became empty and NVIDIA memory returned to
588 MiB used. This avoids leaving a large idle model resident for other GPU
work.

Source evidence round-trip was verified, the durable queue row ended in `DONE`,
and one job was acknowledged. However, the critic status was `failed`, there
were zero successful assessments, and `correctness.all_jobs_completed` was
false. Do not treat queue `DONE` as proof of successful maintenance work: the
application-level outcome must include the assessment result. These historical
Ollama runs validate real embedding/storage behavior but neither tested
Bonsai/Ollama combination met the structured-output contract. The second tag
took much longer without improving correctness. At that point the failure could
not be isolated between model capability/quantization, prompt/schema complexity,
and Ollama structured-output behavior. The later Prism Bonsai 2 experiment below
provides a successful structured-output result at a larger output cap. Raw model
output remains private; safe failure classifications are sufficient for routine
traces.

### Prism Bonsai 2 And Corrected Index Evidence

The earlier Ollama measurements used `bonsai-parser-8k:latest` or
`MichelRosselli/bonsai-27b:Q1_0`; neither was the local Ternary Bonsai 2 model.
The Bonsai 2 check used the self-built Prism `llama.cpp` server, build 10706
(`1a07bfa5f`), with `Ternary-Bonsai-2-27B-PTQ1_0.gguf`, no vision projector,
4,096 context, one slot, Q4 KV cache, Flash Attention, and all 65/65 layers
offloaded to the RTX 3080 Laptop GPU. The model file reports PTQ1_0 1.75 bpw.
`llama.cpp` estimated 5,743 MiB GPU use and 1,214 MiB remaining headroom at
startup. The embedding model remained `intfloat/multilingual-e5-small` on CPU.

The local `ChatOpenAI` adapter uses `json_schema` structured-output mode by
default; the critic receives the Pydantic `ObservationCriticOutput` schema, then
the host validates the parsed response and its cited evidence IDs. The 256-token
completion cap produced OpenAI's `LengthFinishReasonError` (`finish_reason` was
`length`), so no structured assessment was available. Raising only the output
cap to 1,024 yielded one successfully parsed and evidence-validated assessment.
This is direct evidence that the failure was completion truncation at the lower
cap, not a Bonsai 2 inability to follow the JSON schema. It is one successful
maintenance smoke, not yet a semantic-quality evaluation of Bonsai findings.

Three additional independent one-job profiles reused the warm Prism server and
used fresh PostgreSQL schemas. All three passed the same operational gates;
critic latency was tightly grouped (21.73-22.21 s) and total queue drain was
33.15-33.76 s. E5 was reloaded for each profile, with a median load of 28.87 s
and median measured inference time of 10.32 s. A separate sequential three-job
batch completed all jobs in 77.75 s, with 48/48 background conversation index
jobs DONE and semantic self-retrieval verified. Thus the observed bottleneck in
this small workload is materially split between real E5 embedding and the
provider call, not graph persistence alone. That batch made 258 CPU E5 embedding
calls, used 1,114 SQL statements (p95 4.29 ms), and increased process RSS by
about 0.97 GiB. The disposable PostgreSQL container had no per-container memory
or CPU quota configured; this is not a resource-limited deployment benchmark.

The profile now stores only allowlisted critic quality aggregates, not finding
messages, IDs, or model output. Across the three one-job repeats, each assessment
contained one info-level finding, but the overall labels/actions varied: two
were `review_required`/`review_parent`, while one was
`weak_label`/`request_human_review`. The three-job batch returned one `adequate`
assessment with no finding, one `weak_label` with `request_human_review`, and
one `review_required` with a `contradictory` finding and `review_parent`. This
variation may reflect different subjects as well as model variability. Since
the bounded findings' prose is intentionally not in the profile artifact, a
human has not yet verified whether these judgments are substantively correct.
Structured-output success and host-validated evidence references are necessary
protocol checks, not proof of critic quality.

A separate fixed-answer diagnostic called the production critic directly on
four synthetic English frames using the same Ternary Bonsai 2 GGUF and custom
Prism `llama.cpp` server (not Ollama). The initial v1 fixture set scored 2/4.
Review found its clean-node summary added AI/HPC workloads absent from its source,
so the critic's unsupported-detail finding was warranted; the expected-empty
answer was defective. The conflated GPU/CUDA case also had a too-narrow expected
set: `contradictory` is source-supported because the graph claims one combined
entity while the source explicitly distinguishes the two concepts. The original
v1 artifact and score are retained as historical evidence, not an accuracy
baseline.

The corrected v2 fixtures narrowed the clean summary to what the source states
and expanded the conflation rubric to accept `contradictory`, while retaining
source-citation checks. Three sequential runs against the already-loaded server
all scored 4/4 (12/12 case responses): the clean case emitted no findings; the
weak label, conflated concept, and unsupported causal edge each received the
expected source-grounded finding category. The category and evidence match was
stable across all three runs. The median total time per four-case set was 67.87
seconds; median case times were 30.39 s (clean), 11.61 s (weak label), 13.22 s
(conflation), and 12.45 s (unsupported edge). These are warm-server times at
4,096 context and a 1,024-token completion cap. They demonstrate repeatability
on this small synthetic rubric, not production-corpus accuracy, cold-start
performance, or broad semantic reliability. A domain expert should still
review the findings before using this as release-quality evaluation evidence.

### Real E5 And Bonsai2 Maintenance E2E

To verify the maintenance workload beyond the direct critic fixture, the
profile was run three times with three seeded jobs per run, each in a fresh
auto-dropped schema on the dedicated PostgreSQL 17/pgvector container. The
critic was the Ternary Bonsai 2 `PTQ1_0` GGUF served by the custom Prism
`llama.cpp` build at 4,096 context, one sequence, Q4 KV, temperature 0, and a
1,024-token output cap. No vision projector was loaded. The real local
`intfloat/multilingual-e5-small` embedding model ran on CPU, dimension 384,
maximum sequence length 512.

All operational gates passed in every sample: 3/3 maintenance jobs reached
`DONE`, 3/3 critic calls produced validated assessments, every source span was
verified, all 48/48 conversation index jobs reached `DONE`, and semantic
self-retrieval passed. The workers emitted 22 bounded trace events per run.
There were no skipped critics, lease-renewal failures, or pending embedding
jobs. Each profile reported `no_plan` for its three subjects, so this validates
maintenance observation and indexing, not provider-backed cross-link proposal
or patch application.

Across the three samples, queue drain was 75.87-82.10 s (median 75.97 s), with
critic-call p50 15.18-15.89 s (median 15.40 s). Each fresh profile process
loaded E5 in 27.92-28.55 s (median 28.35 s) and spent 25.51-25.57 s (median
25.54 s) encoding 258 items. Median process RSS delta was about 1,001 MiB.
Each sample issued 1,116 SQL statements; SQL statement p95 was 4.00-4.12 ms
(median 4.06 ms). PostgreSQL schema tables occupied about 1.71 MB. PostgreSQL
was not resource-capped: the container had zero configured CPU/memory limits;
the later point-in-time `docker stats` reading was 121 MiB against 15.62 GiB
host capacity and is not a peak measurement. llama-server cold-start/readiness
time was not included in the profile JSON.

Operational outcomes repeated, but critic assessment labels did not remain
stable despite the same 3-job seeded workload and configured temperature 0.
Across nine assessments, verdict counts were `review_required` 4,
`weak_label` 3, and `adequate` 2; the per-subject verdict changed across
replicates. Finding counts were 2, 2, and 3, all low-severity `weak_label`
findings. The raw profile intentionally stores only bounded aggregate labels,
not finding text or evidence IDs. This makes the finding content impossible to
human-audit from the saved profile alone; keep this quality result provisional
until the findings are reviewed through a privacy-safe, local-only artifact.
The fact that the fixed-answer synthetic set was consistent while E2E labels
varied points to a need to test prompt/context sensitivity and critic output
calibration on representative frames, not just provider schema correctness.

A larger single sample used the same setup with ten jobs in one fresh schema.
All 10 jobs completed, all 10 critic assessments passed host validation, all
source spans were verified, and all 146/146 conversation indexing jobs reached
`DONE`; the semantic self-retrieval probe passed. The worker emitted 65 bounded
events, renewed two leases without failure, recorded no omitted observation
context and no pending embedding jobs, and returned `no_plan` for all jobs.
Queue drain was 272.25 s (0.0367 jobs/s). The critic p50/p95 was 18.12/24.91 s;
E5 loaded in 28.40 s, then spent 79.43 s across 804 items; process RSS delta
was 1,009 MiB. PostgreSQL handled 3,399 statements with 4.00 ms p95 and about
2.69 MB of schema tables. Assessment labels were `adequate` 2,
`review_required` 4, and `weak_label` 4; 13 info-level findings comprised 10
`weak_label` and 3 `coverage_gap` findings. This is one larger synthetic sample,
not a repeated scale benchmark, and it still exercises observation rather than
cross-link proposal/application. The DB container had no resource limits as
described above; the workload cannot establish behavior under production
quotas. The input graph is intentionally low-information synthetic data
(`Benchmark concept N` labels and generic maintenance-only source text), so the
low-severity label/coverage findings are a worker-path sanity signal, not a
semantic-accuracy score on meaningful knowledge.

The first run exposed an application classification defect: the exception class
`LengthFinishReasonError` had been treated as a prompt-context overflow, which
could pause background maintenance unnecessarily. The classifier now reports
`output_token_limit`; only actual context-overflow evidence triggers the
background pause. Regression tests cover both outcomes. The profile exposes
`--critic-output-tokens` and records the cap per run.

The focused cross-link run is available as
[`profile_crosslink_maintenance_e2e.py`](../scripts/profile_crosslink_maintenance_e2e.py).
It seeds two source-grounded concepts under an actually committed and active
revision-pinned ParseView, stores the group as human-pending, then exercises a
clearly labeled synthetic reviewer decision, revalidation, graph application,
and conversation-index self-retrieval. It emits bounded live trace events and
heartbeats without logging prompts, excerpts, rationale, or model prose.

**Completed live Bonsai2 profile (2026-10-24):** the provider was the downloaded
`Ternary-Bonsai-2-27B-PTQ1_0.gguf` served by the custom Prism `llama.cpp`
`llama-server` build, not Ollama. The runtime was configured without the vision
tower, with a 4,096-token context, one slot, Q4 K/V cache, full GPU-layer
offload request, and an 800 MiB fit target. This compiles/runs the `llama.cpp`
server; the already-quantized GGUF model itself was not compiled. Bonsai2
generated a one-operation group; deterministic source/revision/scope validation
passed; the independent critic returned `approve`; human-review policy
persisted a pending artifact; the synthetic reviewer decision triggered
revalidation and application; and the accepted cross-link was read back.
Conversation indexing reported 172 `DONE` jobs, zero pending materialized jobs,
and a successful semantic self-retrieval check. The run emitted 44 bounded
trace events. Proposal plus critic took 146.463 s, and approval,
revalidation, and apply took 9.698 s.

This is one successful synthetic end-to-end functionality run, not evidence of
semantic quality on a real corpus, repeated-run reliability, or production
throughput. The embedding was `intfloat/multilingual-e5-small` on CPU, supplied
as an in-process callable. Its provider metadata currently uses a compatibility
label intended for custom/fake callables; do not read that label as evidence
that fake vectors were used. The run exposed and fixed two real-path issues:
empty neighbor context was rejected by the shared context builder, and
generated cross-link edge IDs lacked the workspace namespace prefix required
by patch authorization. A subsequent serializer audit found that persisted
cross-link edges retained only the first of two validated source spans.
Cross-link edge serialization now preserves all validated character-span
pointers, with a regression test verifying both sides. The successful live
sample described below confirms the persisted edge retains both fixture spans.
The fixture also seeds the source revision/readiness artifacts required by the
apply path.

**Repeated known-answer comparison (2026-10-24):** the profiler was extended
to record bounded failure artifacts, continue through every requested sample,
and accept a per-call completion cap. With all other settings held fixed
(Bonsai2 `PTQ1_0`, Prism build `10706`, text-only, 4,096 context, one slot, Q4
K/V, Flash Attention, full GPU-layer offload request, 800 MiB fit target,
medium reasoning effort/budget 2,048, E5-small on CPU, PostgreSQL 17/pgvector
capped at 2 CPU/4 GiB), the 1,024-token cap completed 1/3 samples; two proposal
responses ended with `LengthFinishReasonError`. The remaining sample passed
the critic and full lifecycle. At a 2,048-token per-call cap, all 3/3 fresh-
schema samples passed: each produced one operation, passed deterministic
validation, received critic approval, persisted a human-pending artifact,
passed the synthetic reviewer decision and revalidation, wrote one accepted
edge, retained both exact fixture spans, finished 172/172 index jobs, and
passed semantic self-retrieval. All three edges matched the expected `uses`
direction and predicate. Proposal-plus-critic median was 186.088 s
(158.629–190.973 s); approval/revalidation/apply median was 10.859 s
(10.587–21.886 s). Each run reported ACL audit `disabled`, because the
profiler's core engine currently does not enable ACL. These are repeated
protocol/functionality checks on one synthetic relation, not corpus-level
semantic quality or production capacity evidence. The 2,048-token cap is the
better-evidenced setting for this local configuration; it is not a global
provider-default recommendation.

The live traces emitted `maintenance_stale_claim_rejected` with
`lease_ownership_lost` after a successful job acknowledgement in at least one
sample. Code inspection confirmed a renewal-thread race: the worker could wake
for renewal after the strategy acknowledged the job but before dispatch stopped
the thread. Lease renewal and terminal acknowledgement are now serialized per
claim; acknowledgement stops renewal before marking the job done, and a
regression test forces an in-flight renewal/ack interleave. The full worker
orchestration unit file passes (35 tests).

**Live lease-race confirmation (2026-10-24):** one fresh-schema, one-job
Bonsai2/Prism run completed the selection, evidence, proposal, deterministic
validation, critic, routing, acknowledgement, and queue-drain stages in 205.3
seconds. The trace contained four successful lease renewals, zero
`maintenance_stale_claim_rejected` events, and zero `lease_ownership_lost`
events; the temporary PostgreSQL container and local model server were cleaned
up afterward. This confirms the targeted false post-ack telemetry did not
recur in this sample, but it is only one run. The full profile exited nonzero:
the model critic rejected the synthetic candidate, so there were zero
human-pending groups where the harness expected one. No cross-link was applied.
Treat this as partial lease-race evidence and a real-provider critic outcome,
not as a successful cross-link lifecycle repeat or evidence that the candidate
was semantically wrong. The saved artifact used the generic
`unexpected_pending_group_count` code because the harness classifier recognized
`rejected` but the workflow emits `reject`; the classifier now accepts both
spellings, with a regression test (`8` profile-audit tests pass). The saved live
artifact was not rewritten or rerun after that diagnostic-only correction.
Retain genuine ownership-loss events as operational failures and repeat the
lease check across more than one live run.

The paired E5 runs also corrected a measurement gap: index reconciliation and
status inspection use the exact background conversation namespace, and a
persisted message must be found by a vector self-query before the profile
passes. Three independent three-job samples per mode now give a more stable
small-workload comparison. Two-stage used 138 versus 258 E5 items per sample
(median 16.10 versus 25.53 seconds in the embedding callback), and median
worker drain was 19.30 versus 29.26 seconds. Median send p50 was 5.36 versus
8.32 seconds. SQL statements increased from 1,121 to 1,303 per sample. All
index jobs were already terminal `DONE` before the profile's final reconcile;
the final reconcile applied zero jobs. All nine messages in each mode were
semantically retrievable immediately after `send_lane_message` returned. This
is repeatable evidence for this small local workload, not a production-scale
throughput result. Relative to single-stage, the two-stage medians were about
35.5% lower for send p50, 34.0% lower for queue drain, and 37.0% lower for E5
callback time, with 46.5% fewer embedding items and 16.2% more SQL statements.
The p95 figures are descriptive summaries of only three sends per sample, not
reliable tail-latency estimates.

**Measurement correction:** an earlier background polling probe shared the
writer's graph-engine instance. Namespace scoping on that engine is protected
by a lock, so the probe contended with the maintenance worker and distorted its
own visibility-delay measurements. Its reported multi-second
"admission-to-visibility" values are withdrawn and must not be used as
performance evidence. The probe now performs a single synchronous self-query
after each send returns and records the complete send-call duration separately.
This avoids concurrent observer contention, but it measures visibility after
the API return, not an asynchronous time-to-visibility distribution.

The send path also enters Kogwistar's `defer_index_reconciliation()` context,
which drains queued work on context exit. Therefore this sync lane API waits for
index reconciliation before returning, including in `two_stage` mode. The
current two-stage comparison demonstrates lower work and latency in one
three-job sample, but does not establish a producer/background-worker split or
stable gain. Index backlog age at the end of worker processing is zero because
those writes have already drained it; to measure transient backlog or a genuine
admission-versus-visibility interval, we need an asynchronous producer path or
independent observer and repeated runs. The disposable PostgreSQL container
still has no CPU or memory quota, so this remains an unconstrained local profile.

The live progress event originally labeled the embedding total as
`synthetic_embedding_total_ms` even for E5. That diagnostic label is now
`embedding_model_total_ms` and includes model/device fields. The second smoke
was run after the correction and confirms the live status reflects the actual
E5 model/device and generic embedding-time label.

### Slowness Diagnosis And Targeted Fix

The worker was CPU-bound rather than waiting on the synthetic embedder or
PostgreSQL. The profiled single-job run attributed about 6.5 seconds across 121
calls to `pydantic-extension`'s `_infer_mode_from_stack`, which calls
`inspect.stack()` to guess whether Pydantic validation is happening inside an
LLM structured-output path. Lane-message persistence constructs backend `Node`
and `Edge` models, so this heuristic is unnecessary there. SQL statement
latencies in the same measurement were low (mean 2.47 ms, p95 4.38 ms).

A focused local microbenchmark of 100 `Node` plus 100 `Edge` constructions
confirmed the mechanism across five samples: heuristic mode performed 400 stack
inspections each and had a 2.81 s median; wrapping the same constructions in
`use_mode("backend")` performed zero stack inspections and had a 4.41 ms median.
This is a model-construction benchmark, not an end-to-end speedup claim.
Kogwistar now selects backend mode explicitly
while persisting lane messages, anchors, and semantic edges. A regression test
fails if that path calls the stack heuristic and verifies that the message,
semantic edges, and inbox projection are still persisted.

The explicit backend-mode fix is validated by the maintenance unit tests and
lane-message tests. The 30-job profile now runs on the available disposable
PostgreSQL service, but there is no comparable repeated before/after sample for
isolating the fix's end-to-end speedup. Other worker-side model constructions
may still trigger stack heuristics; inspect a fresh low-overhead profile before
deciding whether further explicit mode scopes are appropriate.

The read-audit timings exposed another use of the same heuristic. A local
five-sample microbenchmark reconstructed 100 valid backend Nodes with
`model_validate`: implicit mode detection had a 1.019 s median, while wrapping
the same batch in `use_mode("backend")` had a 0.005 s median (about 190x lower;
synthetic object construction, not an end-to-end claim). The read subsystem
now explicitly selects backend mode for Node/Edge reconstruction from ordinary
get/query results and raw backend JSON, including ref-filter and reference
context helpers. A core regression test replaces `_infer_mode_from_stack` with
a failure and confirms `get_nodes` plus `get_edges` still reconstruct the
expected models. The four focused read-probe tests passed and Ruff passed.
The matched replay then measured diagnostic audits falling from 204.57 s to
149.19 s and total pre-cleanup wall time falling from 528.24 s to 453.29 s.
The proposal/review phase was essentially unchanged (155.46 s before, 155.49 s
after), while approval/apply was 117.80 s versus 100.42 s. This single pair
confirms a material audit-path improvement but cannot isolate all variance or
establish a production gain. A later fresh-schema replay with raw metadata-only
reads reduced the audit to 0.758 s and found every expected backend row. Mode
selection affects validation overhead, not persistence semantics; the older
ACL-filtered read count is not a row-presence measure.

The 150-item retry was stopped after 15m35s with 88 distinct assessment messages
persisted (58.7% of requested jobs). That is about 5.65 assessed jobs/minute; at
the observed average, completing 150 would take roughly 26.5 minutes. The Python
process accumulated 823.5 CPU-seconds over 935 wall-seconds (about 88% of one
core) and used about 244 MB RSS, so this does not look like a RAM-capacity stall.
At cutoff, the PostgreSQL `index_jobs` table showed 1,626 DONE, 12 DOING, and 50
PENDING rows. These are vector/index operations, not maintenance-job completion
counts. Together with the one-item synthetic embedding measurement, the evidence
rules out slow model inference as the explanation for these profile runs. The
CPU profile and focused construction benchmark subsequently identified
heuristic Pydantic caller-stack detection as a concrete CPU hotspot, and the
lane-message persistence path now opts into backend mode explicitly. The
150-item run itself remains incomplete and cannot quantify the fix; its per-job
hot path must be repeated with streamed traces and timing data after the local
PostgreSQL profile service is available.

The 100-item lane run confirms publication is the dominant measured cost in this
profile: 100 messages took 218.1 s to publish, while 101 acknowledgements took
about 32.6 s to drain. Median publication latency increased from 1.41 s at 10
items to 2.21 s at 100 items. This is a scaling warning, not a demonstrated
quadratic law. The next scale target is one representative 1,000-item run, but
only after repeated 100-item samples establish stable cost and the harness can
run it within a practical budget. A 10,000-item run is optional stress testing,
not a completion gate.

In the 100-item, four-worker drain, claim-batch p50 was 3.92 s and p95 4.11 s;
per-message acknowledgment p50 was 11.9 ms. This points to claim/filter/context
work rather than the individual acknowledgment call as the more promising
investigation. All runs below completed messages exactly once:

| Items / workers | Engine topology | Drain | Scope-acquisition wait p50 | Claim + ack rate | SQL p95 |
|---:|---|---:|---:|---:|---:|
| 10 / 1 | shared | 1.06 s | not instrumented in that sample | 10.39/s | 2.91 ms |
| 10 / 4 | shared | 2.40 s | not instrumented in that sample | 4.59/s | 3.72 ms |
| 10 / 16 | shared | 8.77 s | 3.97 s | 1.25/s | 3.76 ms |
| 10 / 4 | independent engine per worker | 1.82 s | 0.14 ms | 6.03/s | 4.32 ms |
| 10 / 16 | independent engine per worker | 4.43 s | 0.14 ms | 2.49/s | 12.01 ms |

Independent engine construction averaged 1.65 s per engine at 4 workers and
1.61 s per engine at 16 workers. The 16-engine run's before/after RSS delta was
47.7 MB versus 21.3 MB at 4 engines; these are process samples, not peaks. A
short two-item independent-engine smoke also passed after temporary-directory
cleanup was added.

The instrumented 16-worker shared-engine run measured namespace-scope wait at
p95 7.35 s, versus scope-hold p50 0.46 s. This aligns with Kogwistar's current
`scoped_namespace` implementation, which acquires a per-engine reentrant lock
and holds it throughout the scoped block. In the shared-engine harness topology,
that app-level lock serializes worker scopes. With separate engine instances,
the wait disappeared; at 16 engines, however, SQL latency and total drain time
rose again, consistent with backend contention at this small fixture size. DB
lock-wait time itself is not sampled, so that attribution remains provisional.

The independent-engine thread runs approximate separate daemon replicas'
per-engine locking but not process costs. A separate-process sweep confirms the
same direction: four processes took 17.52 s to become ready and drained in
2.00 s; sixteen took 38.55 s to become ready and drained in 3.54 s. Scope wait
remained negligible (p50 about 0.16 ms), while claim p50 rose from 1.05 s to
2.54 s and SQL p95 from 3.16 ms to 6.01 ms. Each process's RSS increase after
Python startup, during engine setup and work, was about 21 MB; this is not total
process RSS or a high-water mark. The evidence suggests a shift from local
namespace-lock serialization to database/query contention as worker engines
multiply, but PostgreSQL lock waits were not captured and each setting has only
one sample.

Compose's standard setup has one maintenance daemon service; horizontally
scaled replicas use separate processes/containers, independent engine instances,
and shared PostgreSQL. The process benchmark is closer to that topology than
the shared-engine thread runs, but still runs on one host and is not a full
container-resource-limited deployment test.
The benchmark applies production-equivalent background namespace scoping in
each worker; without that context, the two-stage diagnostic initially claimed
zero messages despite persisted rows.

## Observability And Workflow Persistence

The delayed-critic runtime-workflow run completed both maintenance jobs, kept
source evidence span-verified, and exercised the durable maintenance-job lease:
26 renewals, zero renewal failures. OTel exported 4 spans through the local
counting exporter with zero drops and zero export errors. The nested Kogwistar
workflow persisted 1 run, 3 step-execution records, and 3 checkpoints (2,210
serialized checkpoint-state bytes). Measured calls averaged about 309 ms per run
record, 434 ms per step record, and 388 ms per checkpoint in that single sample.

This confirms those paths are measurable and run end to end. The paired,
otherwise-identical one-sample telemetry comparison showed 24.06 s with OTel off
and 24.55 s on, with the same 2,653 SQL statements and unchanged 1/3/3 workflow
artifact counts. The approximately 2% difference is too small to distinguish
from run-to-run noise with one sample. It verifies telemetry correctness, not a
reliable telemetry cost estimate.

Lane messages support claim, ack, requeue, and dead-letter operations, but no
explicit lane-message lease-renew API was found. The benchmark reports this as
an API limitation; it does not infer maintenance-job lease behavior from the
separate lane-message mechanism.

## Reviewer Checklist

| Recommendation | Status | Evidence / remaining work |
|---|---|---|
| Profile complete graph/source write path | **Partially measured** | One-subject maintenance writes and lane-message writes measured separately; no repeated 1,000-item end-to-end sample yet. |
| Canonical incident-edge freshness and transaction semantics | **Verified** | PostgreSQL integration test proves projection-independent lookup for node/hyperedge incidence, single endpoint, read-your-own-write, and rollback. A bounded 30-job synthetic profile completed. |
| Backend entity-read model construction | **Targeted optimization and audit attribution measured** | Read-side Node/Edge deserialization explicitly selects backend mode. A five-sample 100-Node microbenchmark was 1.019 s median implicit versus 0.005 s explicit. A matched instrumented replay reduced audit time 204.57 s -> 149.19 s, then metadata-only raw censuses reduced it to 0.758 s; these are diagnostic-path measurements, not product request speedups. The former 8/255-node, 0/65-edge result came from ACL-aware Graph API reads and is consistent with ACL filtering; a fresh raw census found 240/240 nodes and 65/65 edges physically present with active events. No storage-loss finding is supported by the fresh run; same-run ACL-visible/raw counts would make the distinction even stronger. |
| Separate lane inbox from telemetry sink | **Measured separately** | 10- and 100-item lane runs disable telemetry; runtime workflow has a separate OTel run. |
| Evaluate two-stage conversation persistence | **Repeated sync-path comparison done; async boundary unverified** | Three corrected samples per mode: all 9 messages per mode were semantically retrievable after send; two-stage median send p50 5.36 s vs 8.32 s, E5 138 vs 258 items, queue drain 19.30 s vs 29.26 s, SQL 1,303 vs 1,121 per sample. Index jobs are drained before return, so this is not a true asynchronous-admission comparison. Measure an independent producer/consumer boundary next. |
| Measure workflow step/checkpoint persistence | **Measured** | One workflow emitted 3 steps and 3 checkpoints; per-call timings are captured. |
| Measure maintenance claim/ack/lease renewal | **Partially measured** | Claim/ack timings measured; forced renewal run had 26 renewals; ordinary production interval not profiled at scale. |
| Measure best-effort telemetry cost | **Paired exploratory sample done** | One matched sample per mode; no reliable cost estimate until repeated. |
| Test checkpoint frequency sensitivity | **Exploratory comparison done** | A single interval-2 sample produced 2 checkpoints / 1,434 state bytes versus 3 / 2,210 at interval 1; not enough to change production defaults. |
| Representative scale through 150 items | **Incomplete** | The largest completed real-model maintenance profile is 10 jobs / 10 critic calls in 272 s (0.0367 jobs/s); it is one synthetic-content sample. A prior synthetic-provider 1,000-item attempt and a 150-item attempt exceeded 15 minutes; the latter reached 88/150 assessments. No 150-item real-provider workload or 1,000-real-LLM-request benchmark has completed. |
| Live maintenance trace smoke | **Done** | One local-venv entry completed with nine streamed operational events; a fresh 30-job run completed with 63 bounded events; streams exclude payload/source content. |
| Critic outcome quality signals | **Synthetic rubric repeatability measured; production quality unverified** | Three warm-server runs of the corrected four-case Bonsai2/Prism set passed 12/12 expected verdict/evidence checks. Three real 3-job E2E samples showed per-subject verdict variation; the 10-job run reported 13 findings but finding text is intentionally absent from profile output. Human/domain review of local-only findings and representative source cases remains outstanding. |
| Provider-backed cross-link lifecycle | **Repeated live protocol pass on one synthetic fixture; representative quality and capacity remain open** | Two fresh-schema ACL-enabled PostgreSQL/pgvector samples passed with live Bonsai2/Prism and CUDA E5: expected relation/spans, deterministic validation, critic approval, human-pending persistence, synthetic approval, edge application, all 610 index jobs DONE, and semantic self-retrieval. Each used 4K context/Q4 KV, 2,048 output cap, and a 2 CPU/4 GiB database container. Proposal/review was 267.37/267.17 s; approval/apply 107.53/108.41 s. The shared persistent E5 cache had 13/884 and 50/884 hits; CUDA embedding time was 143.91/146.36 s, so no full-path cache speedup was demonstrated. Python and Prism/GPU were not resource-capped. These repeated passes improve protocol confidence but do not establish semantic quality on representative documents or production capacity. Earlier three 2,048-output-cap known-answer samples and the 504.5/186.0 s ACL sample remain historical results; the 2K-total-context failure remains a config-mismatch failure. |
| Deterministic provider replay and indexing attribution | **GPU replay/cache verified; prior apparent storage gap explained by ACL-filtered audit** | Known-answer callbacks made no LLM calls. GPU cache-off/cold/warm runs passed ACL, relation, persistence, indexing, and self-retrieval gates. The latest fresh-schema cache-off run used CUDA E5 (3,476 items, 122.59 s; load 27.63 s), passed 610/610 index jobs and semantic self-retrieval, and found all 240 node/65 edge backend rows with active base-namespace events. The earlier 8-node/0-edge lookup used ACL-aware `engine.read` under ACL-enabled mode and is consistent with access filtering, not missing rows. Controlled repeated-input CUDA trials showed 66.6-68.1% lower engine-path time over five passes, 96/96 warm hits, and exact vector equality. The full generated workload had only 5.6% warm hits, so no reliable end-to-end cache speedup is established. The earlier 528.24 s profile included 204.57 s of expensive diagnostic censuses; a subsequent read-mode run measured 149.19 s, and the latest raw metadata-only census took 0.758 s. Latest profile wall was 307.66 s before cleanup, with workflow phases 265.63 s and censuses under 1 s. These are fixture-replay timings, not live-LLM throughput or general application latency. |
| Live progress heartbeat | **Verified** | One-item smoke streamed per-event summaries; periodic heartbeats report acknowledged jobs, last-event age, CPU/RSS, and embedding activity. |
| 10,000-item stress scale | **Optional / deferred** | Not required to decide the current production profile; run only if 1,000-item results show a scaling question that needs it. |
| Chroma persistence path | **Blocked by core adapter incompatibility** | Full integration test fails because a node metadata field contains a nested mapping (`atomic_retained_child`), which current Chroma metadata validation rejects. This is distinct from the embedding callback signature; normalization must preserve metadata round-trip semantics and belongs at the Kogwistar backend boundary. |
| Workers 1 / 4 / 16 | **Exploratory sweep done** | Ten-item thread and process sweeps passed exact-once checks; separate processes avoid local lock waits but 16-way startup and SQL/claim latency rise. Repeat with larger batches and container limits. |
| DB operations, memory, lock contention | **Partial** | SQL counts/latencies, before/after RSS, and app namespace-lock wait are captured; peak allocations and PostgreSQL lock waits are not. |
| Broad Rust rewrite decision | **No decision** | Current evidence is Python service/storage profiling only and cannot justify or reject an end-to-end Rust rewrite. |

## Next Measurements

1. Measure true two-stage admission separately: use a producer that commits
   canonical/stage-1 state and returns without draining embeddings, then an
   independent worker/reader to measure backlog age and semantic-visibility
   delay. Do not infer asynchronous behavior from the current synchronous
   `send_lane_message` path.
2. Compare telemetry enabled/disabled on the exact same workflow and critic
   workload.
3. Repeat separate-process lane drain at workers 1, 4, and 16 with larger
   prepared inboxes and production-like container memory/CPU limits, preserving
   per-message write boundaries and exact-once checks.
4. Repeat the bounded PostgreSQL maintenance profile at least three times and
   compare identical before/after revisions to quantify the explicit backend-
   mode and indexed incident-query changes. Keep the 150/1,000-item runs deferred
   until repeated bounded samples are stable and complete within budget.
5. Add direct PostgreSQL lock-wait sampling and peak-memory/allocation metrics;
   separately decide whether per-engine namespace serialization is an expected
   correctness tradeoff or needs a safe concurrent namespace-view redesign.
6. Keep Python as the baseline. Consider a narrow Rust rewrite only if repeated
   end-to-end results show a stable bottleneck in CPU-bound Kogwistar logic,
   after database, projection, embedding, and serialization costs are separated.
7. Human-review local-only finding outputs from the repeated 3-job and 10-job
   Bonsai2 runs. Investigate per-subject verdict changes despite configured
   temperature 0, then add representative non-sensitive source/graph cases and
   rerun the calibrated rubric. Keep raw finding prose local unless confirmed
   safe for publication.
8. Capture a matched full-stack resource-limited sample: the two new live
   samples capped PostgreSQL at 2 CPU/4 GiB, but Python and Prism/GPU still ran
   on the host without quotas. Measure llama-server cold-start/readiness and
   application RSS/CPU under intended deployment limits. Do not project a
   150-item runtime from the existing single 10-job sample.
9. Treat the bounded real-provider cross-link functionality/repeatability check
   as complete for its synthetic fixture: three fresh-schema Bonsai2/Prism runs
   at the 2,048-token output cap passed proposal, deterministic evidence/span
   validation, critic approval, pending-review persistence, synthetic approval,
   revalidation, graph write, both exact fixture spans, terminal indexing, and
   semantic self-retrieval. Two new ACL-enabled live GPU-E5 samples also passed
   this lifecycle with the same synthetic relation; they used one shared
   persistent cache and observed 13/884 then 50/884 hits, without a lower
   aggregate E5 compute time on the second run. The 1,024-token comparison passed only 1/3 because
   two provider responses hit the output limit. Preserve that as a model/runtime
   limit finding, not as a cross-link application failure. The ACL-enabled
   model-backed profile has now passed twice through the supported constructor
   path on disposable PostgreSQL; do not scale slow Bonsai inference to many
   jobs merely to repeat this synthetic protocol fixture. The first 2K-context
   launch was too small for a 2K completion plus prompt; the corrected 4K run
   passed. Do not mutate `acl_enabled` after construction or seed ACL internals
   as a benchmark shortcut. The separate user-reported 32K/Q8 vision-off
   configuration was not exercised by this profile and must remain a distinct
   runtime configuration. The previous repository `.env` auth failure is no
   longer a blocker because the successful run used a capped throwaway
   PostgreSQL container. The targeted post-ack stale-claim/lease telemetry fix
   had no recurrence in the two latest 2026-10-24 live samples; continue to
   monitor it in future runs rather than treating two samples as conclusive.
10. Expand from protocol repeatability to semantic quality: run the proposer and
    critic against multiple non-sensitive, representative source-grounded
    fixtures with expected relation direction/predicate and evidence spans.
    Have a domain reviewer assess whether proposed links are useful, not merely
    structurally valid. Keep synthetic known-answer success distinct from
    representative-corpus quality and production-capacity conclusions.
11. **Completed:** deterministic known-answer replay exercises host
    validation, ACL, review, graph apply, indexing, and semantic self-retrieval
    without LLM inference. GPU cache-off, cache-cold, and cache-warm variants all
    passed the correctness gates. They are operational evidence, not model-
    quality evidence or a capacity benchmark.
12. Index jobs grew 0 → 618 during proposal/review → 626 after review decision
    → 640 after apply in the latest replay; explicit reconciliation took 0.014 s.
    GPU E5 and the persistent result-cache path are now measured. A controlled
    repeated-input comparison passed twice with 66.6-68.1% lower engine-path
    time over five passes, while the full generated-conversation workload had
    only 5.6% warm hits. **Still needed:** explain the amount of maintenance-
    conversation indexing fan-out for this two-node fixture and determine
    which artifacts need semantic indexing. The raw metadata-only integrity
    census measured 0.758 s across eight checks and found all 240 node and 65
    edge rows present with active events; the older 8-node/0-edge result was
    from ACL-filtered reads, not a persistence gap. Continue recording embedding
    and index events by lifecycle phase. Do not disable indexing or weaken
    graph/audit persistence merely to improve the benchmark.
