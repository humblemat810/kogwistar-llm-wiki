# Maintenance And Inbox End-to-End Profile

**Run date:** 2026-10-23

**Status:** Interim evidence; production-scale matrix is not complete.

**Latest validation:** Ruff and Python compilation pass for the profiling
harness and embedding configuration module. The full lane-message integration
file currently reports 6 passed, 2 skipped, and 4 failed in 297 s. All four
failures are Chroma-backed cases that reject a nested `atomic_retained_child`
metadata mapping; changing the embedding-function parameter name did not
resolve this storage-adapter failure. A fresh PostgreSQL profile could not be
started in the latest session because Docker API access was denied and no
profile DSN was configured in that shell. Do not treat the 1,000-item target as
run until a new result artifact proves it.

## Scope

This profile follows the reviewer recommendation to measure complete persistence
paths before considering broader runtime optimization. It uses the current
LLM-Wiki and vendored Kogwistar code, disposable PostgreSQL 17 with pgvector,
fresh schema per run, deterministic 2D embeddings, and a fake structured critic.
No production LLM or embedding inference is included. OTel-enabled runs use an
in-process counting exporter, not Grafana or an OTLP network endpoint.

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
| Maintenance + runtime workflow, OTel on, delayed critic | single-stage | 1 + 1 workflow | pass | queue drain 27.54 s, including 2.5 s critic delay and forced 1 s lease renew interval | 2,679 |
| Lane inbox publication and claim/ack | single-stage | 10 | all 11 including warm-up completed exactly once | publish p50 1.41 s, p95 2.05 s; drain 2.40 s | 3,313 |
| Lane inbox publication and claim/ack | two-stage | 10 | all 11 including warm-up completed exactly once | publish p50 1.27 s, p95 1.50 s; drain 3.25 s | 3,099 |
| Lane inbox publication and claim/ack | single-stage | 100 | all 101 including warm-up completed exactly once | publish p50 2.21 s, p95 2.94 s; drain 32.65 s | 29,182 |
| Lane inbox drain, separate processes | single-stage | 10 items, 4 workers | all 11 completed exactly once; pool ready 17.52 s, drain 2.00 s, claim p50 1.05 s | 3,313 |
| Lane inbox drain, separate processes | single-stage | 10 items, 16 workers | all 11 completed exactly once; pool ready 38.55 s, drain 3.54 s, claim p50 2.54 s | 3,469 |
| Observation + runtime workflow | single-stage, OTel off | 1 + 1 workflow | pass; 1 run, 3 steps, 3 checkpoints | queue drain 24.06 s | 2,653 |
| Observation + runtime workflow | single-stage, OTel on | 1 + 1 workflow | pass; 4 spans exported, zero drops/errors | queue drain 24.55 s | 2,653 |

The one-subject maintenance comparison suggests two-stage persistence may reduce
work on this path: SQL statements fell from 495 to 355 and observed job dispatch
from 2.01 s to 1.52 s. This is only one sample per mode with a synthetic fast
embedding, so it is a hypothesis to repeat, not a production performance claim.
Admission enqueue was similar (14.8 ms versus 14.3 ms); this run does not prove
embedding latency is removed from real synchronous admission.

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
| Separate lane inbox from telemetry sink | **Measured separately** | 10- and 100-item lane runs disable telemetry; runtime workflow has a separate OTel run. |
| Evaluate two-stage conversation persistence | **Initial comparison done** | Single sample per mode; repeat with paired samples and real embedding latency before deciding. |
| Measure workflow step/checkpoint persistence | **Measured** | One workflow emitted 3 steps and 3 checkpoints; per-call timings are captured. |
| Measure maintenance claim/ack/lease renewal | **Partially measured** | Claim/ack timings measured; forced renewal run had 26 renewals; ordinary production interval not profiled at scale. |
| Measure best-effort telemetry cost | **Paired exploratory sample done** | One matched sample per mode; no reliable cost estimate until repeated. |
| Test checkpoint frequency sensitivity | **Exploratory comparison done** | A single interval-2 sample produced 2 checkpoints / 1,434 state bytes versus 3 / 2,210 at interval 1; not enough to change production defaults. |
| Representative scale through 1,000 items | **Incomplete** | Lane workload has 100; 1,000-item end-to-end run is the next scale target after repeats establish a practical runtime. |
| 10,000-item stress scale | **Optional / deferred** | Not required to decide the current production profile; run only if 1,000-item results show a scaling question that needs it. |
| Chroma persistence path | **Blocked by core adapter incompatibility** | Full integration test fails because a node metadata field contains a nested mapping (`atomic_retained_child`), which current Chroma metadata validation rejects. This is distinct from the embedding callback signature; normalization must preserve metadata round-trip semantics and belongs at the Kogwistar backend boundary. |
| Workers 1 / 4 / 16 | **Exploratory sweep done** | Ten-item thread and process sweeps passed exact-once checks; separate processes avoid local lock waits but 16-way startup and SQL/claim latency rise. Repeat with larger batches and container limits. |
| DB operations, memory, lock contention | **Partial** | SQL counts/latencies, before/after RSS, and app namespace-lock wait are captured; peak allocations and PostgreSQL lock waits are not. |
| Broad Rust rewrite decision | **No decision** | Current evidence is Python service/storage profiling only and cannot justify or reject an end-to-end Rust rewrite. |

## Next Measurements

1. Repeat single-stage and two-stage maintenance and lane samples at least three
   times per setting; compare medians and variation, not individual runs.
2. Compare telemetry enabled/disabled on the exact same workflow and critic
   workload.
3. Repeat separate-process lane drain at workers 1, 4, and 16 with larger
   prepared inboxes and production-like container memory/CPU limits, preserving
   per-message write boundaries and exact-once checks.
4. Repeat the 100-item lane publication sample, then run one 1,000-item sample
   if the measured runtime is practical. Do not extrapolate linearly from 100.
   Keep 10,000 optional and defer it unless the 1,000-item result exposes a
   scaling question it can answer.
5. Add direct PostgreSQL lock-wait sampling and peak-memory/allocation metrics;
   separately decide whether per-engine namespace serialization is an expected
   correctness tradeoff or needs a safe concurrent namespace-view redesign.
6. Keep Python as the baseline. Consider a narrow Rust rewrite only if repeated
   end-to-end results show a stable bottleneck in CPU-bound Kogwistar logic,
   after database, projection, embedding, and serialization costs are separated.
