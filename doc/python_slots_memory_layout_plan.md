# Python Slots Memory Layout Plan

## Goal

Reduce per-instance memory for high-cardinality application objects while
preserving serialization, monkeypatching, framework behavior, inheritance,
weak references, and cross-interpreter correctness.

This is a measured migration. `__slots__` is not a blanket style rule. A
singleton service saving one dictionary is less valuable than a stable value
object instantiated tens of thousands of times, and an unsafe slot change can
break tests, plugins, Pydantic, pickling, or multiple inheritance.

## Terminology

- **Strict slots**: instances have declared slots and no `__dict__`.
- **Slots plus dict**: common fields use slots, but `__dict__` remains available
  for extension points, monkeypatching, or compatibility.
- **Framework-managed**: layout is owned by Pydantic, an HTTP/server base,
  SQLAlchemy, a callback framework, or another dependency. Do not impose an
  application slot layout.
- **Empty slots**: `__slots__ = ()` on stateless mixins or protocols so the
  base class does not introduce its own instance dictionary.

## Rules

1. Slot high-cardinality value objects first.
2. Do not slot a class only because it has a short `__init__`.
3. Preserve `__dict__` when callers attach, replace, or monkeypatch instance
   attributes.
4. Preserve weak-reference support when it is part of the existing contract.
5. Treat Pydantic model layout as framework-owned.
6. Resolve every base class before slotting a multiple-inheritance leaf.
7. Measure CPython and PyPy separately. PyPy's object strategies and JIT may
   produce a different result from CPython.
8. Do not change serialized fields, stable IDs, equality, hashing, or graph
   contracts as part of a layout optimization.

## Current State

Most application-owned dataclass value objects already use
`@dataclass(..., slots=True)`. This includes:

- embedding profiles, service settings, source units, search hits, and
  grounding records;
- archive namespace and restore reports;
- agent turns and pipeline result records;
- workspace namespace records;
- maintenance decisions, controls, guards, profiles, candidates, and
  invalidation plans;
- parse statistics and ParseView resolution records;
- workbench query, inspection, semantic-lens, review, and interaction records;
- policy bundles and seed results.

These classes should remain slotted. Their behavior forms the regression
baseline for the remaining migration.

## Wave 1: Safe Strict-Slot Conversions

The following production dataclasses have fixed annotated fields, no dynamic
attribute use in application code, and no subclass hierarchy. They are the
lowest-risk conversions.

| Class | File | Proposed layout | Notes |
| --- | --- | --- | --- |
| `ComposeOptions` | `compose/options.py` | `dataclass(frozen=True, slots=True)` | Pure validated options record. |
| `LaunchStep` | `codex/codex_compose_tui.py` | `dataclass(frozen=True, slots=True)` | The list/dict fields remain mutable; slots do not change that semantic. |
| `TuiConfiguration` | `codex/codex_compose_tui.py` | `dataclass(frozen=True, slots=True)` | Fixed configuration fields and deterministic validation. |
| `LlmWikiIdentity` | `configuration/identity.py` | `dataclass(frozen=True, slots=True)` | Immutable principal/scope value object. |
| `MaintenanceJobExecutionContext` | `maintenance/maintenance_strategies.py` | `dataclass(frozen=True, slots=True)` | Fixed execution context passed between strategies. |

For each conversion, test:

- construction, equality, hashing where applicable, `repr`, and attribute
  reads;
- `dataclasses.asdict` and `dataclasses.replace` where used;
- JSON/command rendering where applicable;
- pickling if the object crosses a process boundary;
- rejection of undeclared attributes;
- whether weak references are required.

An unslotted dataclass currently supports weak references because it has the
normal object layout. If compatibility tests prove a class is weak-referenced,
use `weakref_slot=True` with `slots=True`. Otherwise keep strict slots and do
not pay for unused weak-reference support.

## Wave 2: Safe Service Candidates

These stateful classes have a small, fixed set of fields and no observed
dynamic attribute writes. They can use strict explicit slots after focused
tests, but they are low-cardinality services, so they should be migrated only
after Wave 1 measurements justify continuing.

| Class | Proposed fields | Risk |
| --- | --- | --- |
| `CodexBridgeState` | `token`, `settings`, `runner`, `lock` | Low; verify tests do not replace the runner on an instance. |
| `SettingsService` | `pipeline`, `codex_memory`, `_runtime_multimodal_enabled`, `_runtime_otel_enabled`, `path` | Low to medium; operator tests may inject runtime state. |
| `ParseStatisticsStore` | `db_path` | Low; storage facade. |
| `ParseSessionStore` | `metadata`, `workspace_id`, `namespace` | Low; preserve protocol fakes. |
| `ParseViewStore` | `metadata`, `workspace_id` | Low; preserve fake projection stores. |
| `ParseViewResolver` | `workspace_id`, `store` | Low; fixed resolver state. |
| `InvestigationHistoryService` | `engines` | Low; fixed service reference. |
| `GraphSpaceQueryService` | `engines` | Low; fixed service reference. |
| `DisambiguationService` | `engines` | Low to medium; verify test injection. |
| `EvidenceClosureValidator` | `resolver`, `max_refs` | Low; verify protocol substitutes. |

Before converting a service, search tests for `monkeypatch.setattr(instance,
...)`, direct `instance.__dict__` access, and subclass fixtures. If any are
present, move that class to the slots-plus-dict category unless the test is
intentionally tightened to an explicit dependency injection point.

These conversions are optional. If a service has only one instance per
process and the measured saving is negligible, leave it unslotted.

## Classes That Need Slots Plus Dict

The following classes are extensible orchestration surfaces. Tests and runtime
composition replace instance dependencies or methods, and several use
multiple inheritance. If they are optimized, preserve a `__dict__` and weak
reference compatibility.

| Class or family | Why `__dict__` must remain |
| --- | --- |
| `IngestPipeline` | Tests replace `parser`, `_enqueue_maintenance_job`, and other instance behavior; it is assembled from many mixins. |
| `MaintenanceWorker` | Tests replace `runtime`, loaders, engine methods, and failure hooks; worker mixins form a large multiple-inheritance surface. |
| `AgentGateway` | Mixins and fake APIs are injected for MCP/agent tests. |
| `WorkbenchApi` | Composes pipeline, settings, memory, and workbench services that tests substitute. |
| `KnowledgeWorkbench` and `WorkbenchCockpit` | Responder and mutation dependencies are explicit extension points. |
| `MaintenanceDaemon`, `MaintenanceDaemonRuntime`, `ProjectionDaemon`, and `ProjectionWorker` | Long-running services are patched with clocks, workers, engines, and stop behavior. |
| `CodexWorkbenchWorker` and `CodexWorkbenchDispatcher` | Background execution tests replace leases, responders, and dispatch behavior. |
| `CodexProcessRunner`, `CodexAppServerRunner`, and responder classes | Process transports and progress hooks are test and operator extension points. |
| `LlmWikiTelemetry` | Exporter/provider state is reconfigured and replaced during runtime-toggle tests. |
| Projection store hierarchy | `InMemoryMultimodalProjectionStore`, `SQLiteMultimodalProjectionStore`, and `ChromaMultimodalProjectionStore` have subclass-specific state and backend fakes. |
| Encoder implementations | Remote and local encoders are often replaced by fakes; model memory dominates any instance-dictionary saving. |

The target shape, if measurement justifies it, is:

```python
class ExampleService:
    __slots__ = ("common_dependency", "configuration", "__dict__", "__weakref__")
```

This stores common fields directly while keeping uncommon dynamic attributes
possible. It is only effective when base classes do not already provide an
instance dictionary.

For `IngestPipeline`, `MaintenanceWorker`, and `AgentGateway`, first give each
stateless application-owned mixin `__slots__ = ()`. Then add slots only on the
leaf class. Do not put non-empty slot layouts on multiple sibling mixins;
Python can reject multiple inheritance with incompatible instance layouts.

## Implemented Measured Wave

The first measured service wave is implemented with strict slots for fixed
state classes that have no supported instance extension contract:

- `CodexMemoryService`, `LiveTracePrinter`, and `MappingAssetResolver`;
- `MaintenanceStrategyRegistry`;
- `ReviewQueryService`, `SemanticLensService`, and
  `WorkbenchInteractionStore`.

The CPython artifact records the before/after memory and construction timing in
`doc/slots_benchmark_cpython.json`. At 10,000 instances, retained peak memory
fell by approximately 25% to 68% for these classes. Construction timing was
mixed, so no CPU improvement is assumed without a workload-specific profile.
The next step is not to slot orchestration services automatically: profile a
real bounded workload first, then preserve `__dict__` for classes with test
injection, monkeypatching, plugin, or multiple-inheritance contracts.

## Framework-Managed Classes: Do Not Add Application Slots

### Pydantic models

Do not add manual `__slots__` to application models derived from `BaseModel`.
Pydantic owns validation, private attributes, extra-field behavior, schema
generation, copying, and pickling. The following families remain
framework-managed:

- `IngestPipelineRequest` and `MessageEnvelope`;
- `MemoryEvidence` and `CodexMemoryRecord`;
- all disambiguation contract models;
- `SourceRegion`, `ParseTarget`, parse generation/member/session/frontier
  models, and `ParseView` models;
- parse reconciliation models;
- maintenance scope, provenance, patch, operation, validation, and apply
  result models;
- seed bundle models;
- cockpit action, observation, and turn result models.

If Pydantic model memory is a problem, measure `model_config`, input retention,
and object lifetime first. Do not override the framework's layout.

### External base classes

Leave these unslotted unless the upstream framework documents a supported
layout:

- `_BridgeHandler`, derived from `BaseHTTPRequestHandler`;
- `_BridgeServer`, derived from `ThreadingHTTPServer`;
- `ProviderUsageCallback`, derived from LangChain's callback handler;
- `LongRunJsonlTraceSink`, derived from the Kogwistar sink;
- SQLAlchemy declarative classes in dependencies;
- any class generated or wrapped by FastMCP, OpenTelemetry, Torch, or
  Transformers.

### Protocols, enums, and exceptions

Protocols define typing contracts and are not allocation targets. Enums and
exceptions are also not useful first-wave memory targets. Do not add slots to
them merely for consistency.

Stateless application-owned mixins may receive `__slots__ = ()` only as part
of a measured leaf-class migration. This is inheritance hygiene, not a direct
memory optimization by itself.

## Classes to Leave Unchanged Despite Being Technically Slottable

Some classes have fixed fields but too few live instances for the migration
risk to pay back:

- HTTP, daemon, projection, and workbench service singletons;
- local Qwen encoder objects holding multi-gigabyte model state;
- remote encoder clients with one instance per process;
- CLI parser/build-plan helpers instantiated only during startup;
- archive and migration coordinators that live for one operation.

They may be reconsidered only if heap profiles show many retained instances.

## Implementation Sequence

1. Add memory-layout contract tests for the five Wave 1 dataclasses.
2. Convert one module at a time to `slots=True`.
3. Run focused unit tests plus Ruff after every module.
4. Run the deterministic CI marker before merging Wave 1.
5. Benchmark instance memory and construction time on CPython.
6. Stop if savings are insignificant or compatibility breaks.
7. Profile a real bounded ingest/maintenance fixture to identify retained
   service instances.
8. Convert Wave 2 service candidates only when profiles show a meaningful
   population.
9. Treat slots-plus-dict orchestration work as a separate change because it
   alters inheritance layout.
10. Repeat benchmarks on PyPy when the PyPy runtime profile exists.

## Tests

Add provider-free tests for:

- strict classes lacking `__dict__`;
- slots-plus-dict classes accepting a temporary extension attribute;
- weak references where required;
- pickle round trips for multiprocessing/job payloads;
- `copy.copy` and `copy.deepcopy` where used;
- dataclass `asdict`, `replace`, equality, hashing, and repr;
- Pydantic model dump, copy, validation, and JSON schema stability;
- multiple-inheritance construction for pipeline, worker, and gateway;
- monkeypatch-based dependency replacement used by existing tests;
- no changes to stable IDs, canonical JSON, graph payloads, or provenance.

Suggested layout assertions:

```python
assert not hasattr(ComposeOptions(), "__dict__")

pipeline = build_test_pipeline()
assert hasattr(pipeline, "__dict__")
pipeline._layout_probe = object()
del pipeline._layout_probe
```

Do not assert an exact byte count in ordinary CI. Python build options,
platforms, allocators, and interpreters differ. Keep exact measurements in a
benchmark report and assert only structural contracts in unit tests.

## Benchmark Method

For each candidate:

- allocate 1, 100, 10,000, and 100,000 instances where realistic;
- measure construction time and retained memory after garbage collection;
- measure with `tracemalloc` on CPython;
- also record process RSS because referenced containers dominate shallow
  `sys.getsizeof` results;
- run enough iterations for PyPy to warm its JIT;
- report strict slots, slots plus dict without extras, slots plus dict with one
  dynamic extra, and the original class;
- include object lifetime and peak concurrent count from a representative
  workflow.

Promote a migration only when it saves at least 10 percent for that object's
retained layout or produces a meaningful process-level saving in a realistic
fixture without a material construction-time regression.

### Recorded baseline

The CPython 3.13 comparison was refreshed in
`doc/slots_benchmark_cpython.json` using the migrated classes and equivalent
unslotted baselines at populations of 100 and 10,000. At 10,000 instances, the
observed retained-memory reduction was approximately 16% to 68% across the
migrated classes. The benchmark creates fresh mutable list/dict fields in both
the slotted and unslotted constructors so the comparison does not give either
layout an artificial container-sharing advantage.

The report records wall-clock time and process CPU time per instance. The first
run shows that construction speed is mixed: slots are a memory-layout
optimization and must not be described as a universal CPU-speed improvement.
Repeat the same command on PyPy after its runtime profile is available and
compare the same populations and interpreter metadata.

The recorded 10,000-instance CPython 3.13 run provides this speed baseline
(unslotted -> slotted, microseconds per constructed instance):

| Class | Wall time | Process CPU time | Peak allocation reduction |
| --- | ---: | ---: | ---: |
| `CodexBridgeState` | 3.607 -> 4.085 | 0.03125 -> 0.046875 s | 16.6% |
| `CodexMemoryService` | 14.687 -> 4.163 | 0.140625 -> 0.046875 s | 58.3% |
| `ComposeOptions` | 4.994 -> 5.868 | 0.046875 -> 0.062500 s | 24.9% |
| `LaunchStep` | 1.643 -> 2.948 | 0.015625 -> 0.031250 s | 35.5% |
| `LiveTracePrinter` | 2.295 -> 3.089 | 0.031250 -> 0.031250 s | 67.5% |
| `LlmWikiIdentity` | 2.745 -> 2.614 | 0.031250 -> 0.015625 s | 29.3% |
| `MaintenanceJobExecutionContext` | 2.685 -> 2.805 | 0.031250 -> 0.031250 s | 31.5% |
| `MaintenanceStrategyRegistry` | 1.731 -> 5.364 | 0.015625 -> 0.046875 s | 49.9% |
| `MappingAssetResolver` | 1.457 -> 4.815 | 0.015625 -> 0.046875 s | 48.0% |
| `ReviewQueryService` | 0.948 -> 3.198 | 0.015625 -> 0.031250 s | 68.2% |
| `SemanticLensService` | 1.956 -> 3.474 | 0.015625 -> 0.031250 s | 61.1% |
| `TuiConfiguration` | 3.776 -> 3.863 | 0.046875 -> 0.031250 s | 24.9% |
| `WorkbenchInteractionStore` | 1.010 -> 3.441 | 0.000000 -> 0.031250 s | 68.2% |

These are one benchmark run, not a performance guarantee. The JSON report is
the authoritative artifact and should be refreshed on the target interpreter
before making a release-level performance claim.

### Frequency gate for future waves

The migrated service classes are mostly retained once per process. Their
synthetic 10,000-instance savings validate the layout change, but do not prove
that another broad service migration will materially reduce host memory. The
remaining optimization goal is considered complete unless a process-level
profile identifies an unslotted, high-cardinality application class. Future
migrations must record retained counts and before/after process memory, and
must not trade away dependency-injection or serialization behavior for a
synthetic benchmark result. See `doc/slots_frequency_audit.md` for the current
construction-site audit.

To refresh the CPython report:

```powershell
.venv\Scripts\python.exe scripts\benchmark_slots.py `
  --count 100 --count 10000 `
  --json-out doc\slots_benchmark_cpython.json
```

For PyPy, use the same counts with a warm-up population and record the
interpreter metadata in a separate report:

```powershell
pypy3 scripts\benchmark_slots.py `
  --warmup-count 10000 --count 1000 --count 10000 `
  --json-out doc\slots_benchmark_pypy312.json
```

## Review Checklist

- [ ] The class is application-owned.
- [ ] Its base classes have compatible layouts.
- [ ] All assigned instance fields are declared.
- [ ] Dynamic attribute writes were searched in source and tests.
- [ ] Weak-reference behavior was checked.
- [ ] Pickle/copy behavior was checked.
- [ ] Framework serialization is unchanged.
- [ ] Existing monkeypatch and dependency-injection seams still work.
- [ ] Memory was measured, not estimated from `sys.getsizeof` alone.
- [ ] CPython CI passes.
- [ ] PyPy is benchmarked separately when available.

## Non-Goals

- No manual slots on Pydantic models.
- No slot conversion of third-party framework subclasses without upstream
  support.
- No removal of testable dependency-injection seams.
- No graph schema or serialization changes.
- No broad mixin-layout rewrite in the same change as Wave 1.
- No claim that CPython memory savings automatically apply to PyPy.
