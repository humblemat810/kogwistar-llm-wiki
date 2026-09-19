# PyPy and Slots Validation Report

**Run date:** 2026-09-19

This report records the current validation evidence for the Python 3.12/PyPy
compatibility work and the first measured `__slots__` migration. It is an
engineering checkpoint, not a production-support declaration.

## Test Evidence

### LLM-Wiki

The deterministic provider-free CI slice completed successfully:

```text
696 passed, 4 skipped, 114 deselected, 459 warnings
18m27s
```

The focused regression set for the new profile, slots, and publishing checks
completed successfully:

```text
21 passed in 0.60s
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
`a2cdf226f11880d394f14a004f95030de0359bf9`. The parser commit
`48859213e32a81858ea2811f58868e54388fadbb` and root workflow pins reference
that exact core revision, and all three feature branches are remotely
available. The PyPy workflow remains manual, so its native probe is still
pending.

## Slots Measurement

The checked-in source is `doc/slots_benchmark_cpython.json`. It compares each
migrated class with an equivalent unslotted baseline at 10,000 instances on
CPython 3.13.3. The primary result is retained-memory reduction measured with
`tracemalloc`; construction speed is deliberately not treated as a universal
improvement.

| Class | Slotted peak bytes | Unslotted peak bytes | Peak reduction | Slotted wall us/instance | Unslotted wall us/instance | Slotted CPU s/10k | Unslotted CPU s/10k |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CodexBridgeState` | 2,015,104 | 2,414,960 | 16.6% | 3.787 | 3.481 | 0.046875 | 0.031250 |
| `CodexMemoryService` | 736,094 | 1,765,160 | 58.3% | 14.070 | 3.967 | 0.140625 | 0.046875 |
| `ComposeOptions` | 1,685,408 | 2,245,408 | 24.9% | 4.017 | 5.058 | 0.046875 | 0.046875 |
| `LaunchStep` | 725,264 | 1,125,264 | 35.5% | 2.286 | 2.214 | 0.015625 | 0.031250 |
| `LiveTracePrinter` | 495,096 | 1,525,160 | 67.5% | 3.291 | 3.775 | 0.031250 | 0.031250 |
| `LlmWikiIdentity` | 965,328 | 1,365,472 | 29.3% | 2.656 | 3.641 | 0.015625 | 0.046875 |
| `MaintenanceJobExecutionContext` | 1,045,480 | 1,525,480 | 31.5% | 3.041 | 3.187 | 0.031250 | 0.031250 |
| `MaintenanceStrategyRegistry` | 1,045,464 | 2,085,160 | 49.9% | 1.964 | 3.479 | 0.015625 | 0.031250 |
| `MappingAssetResolver` | 1,125,408 | 2,165,160 | 48.0% | 2.407 | 4.152 | 0.031250 | 0.046875 |
| `ReviewQueryService` | 485,264 | 1,525,160 | 68.2% | 1.459 | 4.392 | 0.015625 | 0.031250 |
| `SemanticLensService` | 655,112 | 1,685,160 | 61.1% | 3.175 | 5.420 | 0.031250 | 0.062500 |
| `TuiConfiguration` | 1,445,408 | 1,925,408 | 24.9% | 4.178 | 4.197 | 0.046875 | 0.031250 |
| `WorkbenchInteractionStore` | 485,264 | 1,525,160 | 68.2% | 1.013 | 3.735 | 0.015625 | 0.031250 |

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
- `.github/workflows/pypy-beta.yml` is manual and non-required;
- the separate PyPy CI Docker workflow is manual and publishes no production
  release tag;
- no local PyPy 3.12 runtime was available for a native run;
- Docker Engine was unavailable locally, so the experimental image was not
  built here;
- the feature-branch pins reference the NumPy-free Kogwistar commit and are
  available remotely, but the PyPy workflow has not yet been run;
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
