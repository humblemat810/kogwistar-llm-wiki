# Maintenance And Inbox End-to-End Profile

**Run date:** 2026-10-23

**Status:** Interim evidence; production-scale matrix is not complete.

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
quadratic law. A 1,000- or 10,000-item full graph publication run was not
attempted because the observed 100-item runtime already makes those samples
materially expensive.

In the 100-item, four-worker drain, claim-batch p50 was 3.92 s and p95 4.11 s;
per-message acknowledgment p50 was 11.9 ms. This points to claim/filter/context
work rather than the individual acknowledgment call as the more promising
investigation. PostgreSQL lock-wait time was not sampled directly, so the source
of claim latency is not yet proven. The benchmark applies production-equivalent
background namespace scoping in each worker thread; without that context, the
two-stage diagnostic initially claimed zero messages despite persisted rows.

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
| Profile complete graph/source write path | **Partially measured** | One-subject writes measured; no 1,000-step repeat yet. |
| Separate lane inbox from telemetry sink | **Measured separately** | 10- and 100-item lane runs disable telemetry; runtime workflow has a separate OTel run. |
| Evaluate two-stage conversation persistence | **Initial comparison done** | Single sample per mode; repeat with paired samples and real embedding latency before deciding. |
| Measure workflow step/checkpoint persistence | **Measured** | One workflow emitted 3 steps and 3 checkpoints; per-call timings are captured. |
| Measure maintenance claim/ack/lease renewal | **Partially measured** | Claim/ack timings measured; forced renewal run had 26 renewals; ordinary production interval not profiled at scale. |
| Measure best-effort telemetry cost | **Paired exploratory sample done** | One matched sample per mode; no reliable cost estimate until repeated. |
| Test checkpoint frequency sensitivity | **Exploratory comparison done** | A single interval-2 sample produced 2 checkpoints / 1,434 state bytes versus 3 / 2,210 at interval 1; not enough to change production defaults. |
| Sizes 100 / 1,000 / 10,000 | **Incomplete** | Lane workload has 100; 1,000 and 10,000 full graph writes deferred due measured cost. |
| Workers 1 / 4 / 16 | **Incomplete** | Four-worker claim/ack measured; one- and sixteen-worker matched samples remain. |
| DB operations, memory, lock contention | **Partial** | SQL counts/latencies and before/after RSS are captured; peak allocations and DB lock waits are not. |
| Broad Rust rewrite decision | **No decision** | Current evidence is Python service/storage profiling only and cannot justify or reject an end-to-end Rust rewrite. |

## Next Measurements

1. Repeat single-stage and two-stage maintenance and lane samples at least three
   times per setting; compare medians and variation, not individual runs.
2. Compare telemetry enabled/disabled on the exact same workflow and critic
   workload.
3. Run lane drain on the same prepared fixture at workers 1, 4, and 16, while
   preserving per-message write boundaries and exact-once checks.
4. Profile 1,000-item lane publication only after identifying an instrumentation
   or setup improvement that can make the run practical; do not extrapolate
   linearly from 100. Keep 10,000 as a deferred stress target.
5. Add direct PostgreSQL lock-wait sampling and peak-memory/allocation metrics
   before attributing the claim-batch latency to a specific lock or subsystem.
6. Keep Python as the baseline. Consider a narrow Rust rewrite only if repeated
   end-to-end results show a stable bottleneck in CPU-bound Kogwistar logic,
   after database, projection, embedding, and serialization costs are separated.
