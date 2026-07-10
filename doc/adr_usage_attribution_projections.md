# ADR: Incremental Usage Attribution Projections

- Status: accepted
- Date: 2026-07-10
- Scope: `kogwistar` runtime envelope and `kogwistar-llm-wiki` projections

## Context

LLM-Wiki needs reliable usage views at several scopes: source document,
logical operation, maintenance job, dream job, and complete runtime run.
Kogwistar already owns budget events, runtime accounting, monotonic entity-event
sequences, and named projection metadata. LLM-Wiki owns product identifiers,
maintenance taxonomy, dream-cycle semantics, and reporting.

The usage view must not become a second accounting system. It must also avoid
replaying the entire history on every inspection or losing progress after a
projector restart.

## Decision

Kogwistar provides the reusable usage-event envelope. LLM-Wiki persists raw
usage events in a dedicated entity-event namespace and materializes a named,
incremental usage projection from those events.

Raw usage events are the accounting source. The usage projection, document
statistics, maintenance summaries, and dream reports are read-side views.
They may be discarded and rebuilt without changing graph truth or workflow
history.

## Event Attribution

Each usage event may carry these dimensions:

- `workspace_id`
- `source_document_id`
- `operation_id`
- `operation_kind`
- `maintenance_job_id`
- `dream_job_id`
- provider and model

`run_id` remains the Kogwistar runtime execution identity. `event_id` is the
idempotency identity for the raw event.

One event represents one atomic accounting observation. Document, operation,
maintenance-job, dream-job, and run totals are grouping views over the same
event; the event is never debited once per grouping dimension.

An event without a domain attribution is included in the explicit
`unattributed` view. The system must not infer a document, job, or dream ID from
nearby events.

## Checkpoint Contract

The named projection record is keyed by workspace and contains:

- `projection_id`
- `workspace_id`
- `projection_schema_version`
- `source_namespace`
- `source_from_seq`
- `source_to_seq`
- `last_authoritative_seq`
- `last_materialized_seq`
- `projected_at_ms`
- `last_source_event_ts_ms`
- `snapshot_id`
- `raw_event_count`
- `materialization_status`
- optional `rebuild_reason`

`last_materialized_seq` is the resume boundary. `projected_at_ms` records when
the snapshot was generated. `last_source_event_ts_ms` records the newest
source event included in that snapshot. The timestamp is observability data;
the sequence watermark is the correctness boundary.

## Incremental Materialization

The projector:

1. Reads the existing named projection.
2. Captures the current raw-event sequence as `source_to_seq`.
3. Reads events in `(last_materialized_seq, source_to_seq]`.
4. Applies each event once using `event_id`.
5. Replaces the aggregate payload and checkpoint together.

Events arriving after the captured watermark are processed by the next cycle.
The projector reports `catching_up` when a newer source sequence exists after
the batch was captured.

If materialization fails, the previous aggregate and watermark remain usable;
the projection status becomes `failed` without advancing the checkpoint. A
missing or incompatible projection is rebuilt from sequence zero. An explicit
full rebuild is available for repair and verification.

The supported statuses are:

- `materialized`: snapshot includes all events through its watermark
- `catching_up`: newer raw events exist beyond the snapshot watermark
- `failed`: the prior snapshot remains available but the latest attempt failed
- `rebuilding`: a full replay is in progress

Two snapshots with the same source watermark and schema version must have the
same aggregate totals.

## Projection Dimensions

The materialized snapshot exposes aggregates for:

- `document`
- `operation`
- `maintenance_job`
- `dream_job`
- `run`
- `unattributed`

Every aggregate reports event count, token totals, cost, runtime, provider/model
sets, and event/unit counts. Unknown provider cost remains unknown rather than
being silently converted into zero-cost evidence.

## Ownership Boundaries

Kogwistar owns:

- `BudgetEvent` and attribution envelope
- provider usage adaptation
- token, cost, time, and budget arithmetic
- monotonic event sequence and named-projection substrate

LLM-Wiki owns:

- document and source identity
- maintenance-job and dream-job identifiers
- raw usage-event namespace routing
- usage aggregation dimensions and reports
- CLI/debug presentation

The usage projection is separate from the graph-content projection, wisdom
artifacts, Obsidian output, and workflow checkpoints. Wisdom may consume usage
or execution outcomes as evidence, but it is not the usage ledger.

## Consequences

Usage inspection can resume from the last successful sequence instead of
rebuilding all history. A failed projector does not destroy the last usable
snapshot, and a full replay remains possible for repair or schema migration.

The raw event stream must be retained for as long as historical usage views
need to be reproducible. Provider integration must propagate real usage
metadata; otherwise the projection can report runtime duration while token and
cost fields remain unknown or zero.

## Verification

Tests must cover incremental batches, late events, idempotent replay, failed
checkpoint preservation, incompatible-schema rebuilds, timestamp and watermark
reporting, and reconciliation across document, operation, maintenance-job,
dream-job, run, and unattributed views.
