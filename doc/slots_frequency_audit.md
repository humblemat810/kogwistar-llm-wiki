# Slots Frequency Audit

## Purpose

This audit checks whether the remaining `__slots__` work is likely to reduce
real process memory, rather than only improve a synthetic 10,000-instance
benchmark. A service object can show a large per-instance saving while still
having negligible application impact when one instance is retained per
process.

## Method

The estimate combines source construction-site searches with the existing
unit-test search results. It is a coarse production-cardinality estimate, not
a runtime profiler. The benchmark in `doc/slots_benchmark_cpython.json`
remains useful for validating layout behavior, but it must not be used alone
to prioritize singleton services.

## Observed production construction counts

| Class | Observed production construction sites | Cardinality assessment |
| --- | ---: | --- |
| `CodexMemoryService` | 1 | one per workbench API |
| `SemanticLensService` | 1 | one per ingest pipeline |
| `ReviewQueryService` | 1 | one per inspection service |
| `WorkbenchInteractionStore` | 2 | one per workbench/background path |
| `MaintenanceStrategyRegistry` | 1 | one per maintenance strategy setup |
| `CodexBridgeState` | 1 | one per bridge process |
| `MappingAssetResolver` | 0 observed | test/helper construction only |
| `LiveTracePrinter` | several optional sites | low process cardinality; depends on tracing configuration |

The existing high-cardinality application records are already represented by
slotted dataclasses or Pydantic-managed models. The service classes above are
therefore safe cleanup targets, but not evidence for a second broad migration
wave.

The core scan found two additional high-value row DTOs and they are now
slotted: `engine_sqlite.IndexJobRow` and
`engine_sqlite.ProjectedLaneMessageSqlRow`. Both are frozen database-result
records repeatedly materialized by queue and projection reads. The public
`messaging.ProjectedLaneMessageRow` was already covered for the same reason.
The corresponding Postgres mutable row views and in-memory state records are
deferred until a process-level profile confirms their retained counts and
until backend mutation/serialization coverage is expanded.

`conversation.ContextItem` is deliberately not included in this wave because
the current packing path copies it through `it.__dict__`; converting it would
require a separate `dataclasses.replace` refactor and compatibility tests.
`conversation.ContextMessage` is a possible future candidate, but current
source/test construction evidence is not enough to establish a high retained
cardinality.

## Decision

Keep the current measured slots wave. Do not convert more orchestrator or
service classes solely because the synthetic benchmark reports a large
percentage. Stop the broad slots goal here unless a process-level profile or
retained-object count identifies an unslotted high-cardinality class.

Future candidates must meet all of these conditions before migration:

1. They are application-owned and have a repeated retained count in a real
   workload, not only repeated test construction.
2. Their instance layout is stable and does not rely on dynamic attributes,
   weak references, or framework injection.
3. A before/after process-level memory measurement shows a material benefit.
4. Serialization, copying, and the relevant CPython/PyPy test matrix remain
   green.

The current measurements record memory, wall time, and process CPU time. They
show that slots are not a universal speed optimization: construction time is
mixed. No additional slot conversion is justified by the present frequency
evidence.
