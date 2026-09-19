# PyPy and Slots Validation Report

**Run date:** 2026-09-19

This report records the current validation evidence for the Python 3.12/PyPy
compatibility work and the first measured `__slots__` migration. It is an
engineering checkpoint, not a production-support declaration.

## Test Evidence

### LLM-Wiki

The deterministic provider-free CI slice completed successfully in the latest
LLM-Wiki run:

```text
Both Python 3.12 and 3.13 jobs passed in run `35445167945`.
```

The focused regression set for the new profile, slots, and publishing checks
completed successfully:

```text
22 passed in 0.60s
```

Ruff (`E4,E7,E9,F`, Python 3.12 target), workflow YAML parsing, and root
`git diff --check` completed successfully.

### kg-doc-parser

The Python-floor, lazy-PDF-dependency, and packaging-import checks completed
successfully with the local Kogwistar checkout on `PYTHONPATH`:

```text
5 passed in 8.00s
```

`poetry check --lock` also completed successfully. Poetry emitted existing
configuration deprecation warnings only. The parser metadata now declares
Python `^3.12`, tests Python 3.12 and 3.13 in CI, and excludes `pikepdf` from
the PyPy profile while retaining it for CPython PDF fallback support.

### Kogwistar

The local NumPy-free embedding normalization regression set completed:

```text
4 passed
```

The corresponding core metadata and normalization changes are committed as
`80c08c1371148c2a60413f5101e6ec56adf85797`. The parser commit
`845f0bf344a7ce64f67c42fedd342a09129854de` and root workflow pins reference
that exact core revision, and all three feature branches are remotely
available. The automatic Kogwistar PyPy probe initially ran in
`35443844532`; its CPython and Rust jobs passed, while
`pypy-beta-best-effort` reached native-extension verification and failed.
Follow-up run `35447957681` built a real PyPy wheel after the CI-only PyO3
override and completed diagnostics, but native import verification still
failed. The explicit Python-authority fallback suite also failed, so this is
not yet a usable PyPy execution profile. The PyPy profile remains correctly
experimental rather than claiming native compatibility without evidence.

## Slots Measurement

The checked-in source is `doc/slots_benchmark_cpython.json`. It compares each
migrated class with an equivalent unslotted baseline at 10,000 instances on
CPython 3.13.3. The primary result is retained-memory reduction measured with
`tracemalloc`; construction speed is deliberately not treated as a universal
improvement.

| Class | Slotted peak bytes | Unslotted peak bytes | Peak reduction | Slotted wall us/instance | Unslotted wall us/instance | Slotted CPU s/10k | Unslotted CPU s/10k |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CodexBridgeState` | 2,015,104 | 2,415,104 | 16.6% | 3.607 | 4.085 | 0.031250 | 0.046875 |
| `CodexMemoryService` | 736,046 | 1,765,160 | 58.3% | 14.687 | 4.163 | 0.140625 | 0.046875 |
| `ComposeOptions` | 1,685,408 | 2,245,408 | 24.9% | 4.994 | 5.868 | 0.046875 | 0.062500 |
| `LaunchStep` | 725,264 | 1,125,264 | 35.5% | 1.643 | 2.948 | 0.015625 | 0.031250 |
| `LiveTracePrinter` | 495,096 | 1,525,160 | 67.5% | 2.295 | 3.089 | 0.031250 | 0.031250 |
| `LlmWikiIdentity` | 965,472 | 1,365,472 | 29.3% | 2.745 | 2.614 | 0.031250 | 0.015625 |
| `MaintenanceJobExecutionContext` | 1,045,480 | 1,525,480 | 31.5% | 2.685 | 2.805 | 0.031250 | 0.031250 |
| `MaintenanceStrategyRegistry` | 1,045,320 | 2,085,160 | 49.9% | 1.731 | 5.364 | 0.015625 | 0.046875 |
| `MappingAssetResolver` | 1,125,264 | 2,165,160 | 48.0% | 1.457 | 4.815 | 0.015625 | 0.046875 |
| `ReviewQueryService` | 485,264 | 1,525,160 | 68.2% | 0.948 | 3.198 | 0.015625 | 0.031250 |
| `SemanticLensService` | 654,968 | 1,685,160 | 61.1% | 1.956 | 3.474 | 0.015625 | 0.031250 |
| `TuiConfiguration` | 1,445,408 | 1,925,264 | 24.9% | 3.776 | 3.863 | 0.046875 | 0.031250 |
| `WorkbenchInteractionStore` | 485,264 | 1,525,160 | 68.2% | 1.010 | 3.441 | 0.000000 | 0.031250 |

The benchmark used counts of 100 and 10,000 with `warmup_count=0` because this
artifact is a CPython baseline. The peak column is the retained `tracemalloc`
allocation for the batch, including the unslotted instance dictionary; the
wall-time columns are construction time only. Slots reduced memory for every
class in this wave, but construction became slower for `CodexMemoryService`,
`LiveTracePrinter`, and a few other small objects. PyPy must be measured
separately after a working PyPy 3.12 runtime profile exists, with a warm-up
population such as:

```powershell
pypy3 scripts\benchmark_slots.py `
  --warmup-count 10000 --count 1000 --count 10000 `
  --json-out doc\slots_benchmark_pypy312.json
```

## PyPy Status

PyPy 3.12 remains experimental and opt-in:

- the required source CI matrix now covers CPython 3.12 and 3.13;
- the core Kogwistar CI now runs an automatic, non-blocking PyPy 3.12 native
  probe; the latest run built the wheel but failed native import verification
  and the explicit Python-authority fallback suite; the LLM-Wiki application
  workflow remains manual and non-required;
- the separate PyPy CI Docker workflow is manual and publishes no production
  release tag;
- no local PyPy 3.12 runtime was available for a native run;
- Docker Engine was unavailable locally, so the experimental image was not
  built here;
- the feature-branch pins reference the NumPy-free Kogwistar commit and are
  available remotely; the beta lane applies the temporary
  `rpds-py==2026.5.1` compatibility constraint;
- PyO3/Maturin installed-wheel loading and the pgvector/storage boundary still
  require real PyPy probes.

The supported claim is therefore **CPython 3.12/3.13 source compatibility**,
not production PyPy compatibility. The PyPy profile intentionally excludes
NumPy, Chroma, Torch, and other unsupported optional native paths.

## Reproduction Links

- [PyPy support plan](pypy_3_12_support_action_plan.md)
- [PyPy compatibility matrix](pypy_3_12_compatibility_matrix.md)
- [Slots memory-layout plan](python_slots_memory_layout_plan.md)
- [CPython benchmark artifact](slots_benchmark_cpython.json)
