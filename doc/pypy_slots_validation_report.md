# PyPy and Slots Validation Report

**Run date:** 2026-09-21

This report records the current validation evidence for the Python 3.12/PyPy
compatibility work and the first measured `__slots__` migration. It is an
engineering checkpoint, not a production-support declaration.

## Test Evidence

### LLM-Wiki

The deterministic provider-free CI slice completed successfully in GitHub run
`35525514027` for feature commit `a4c6e39`:

```text
Both Python 3.12 and 3.13 jobs passed, along with lint, Rust checks, and the
resource-report comparison job.
```

The non-required experimental PyPy workflows and slot benchmark also completed
successfully for this revision:

```text
PyPy 3.11: 35525513959
PyPy 3.12 beta: 35525514040
Slots: 35525514019
```

These remain non-gating evidence and do not authorize a production PyPy image.

The focused regression set for the new profile, slots, and publishing checks
completed successfully:

```text
61 passed in 15.98s
```

The refreshed full deterministic provider-free slice completed locally on
2026-09-21:

```text
712 passed, 4 skipped, 127 deselected in 11:05
average wall 927.890 ms; average CPU 889.774 ms; average CPU utilization 97.8%
average RSS after 638.46 MiB; average RSS delta 184.2 KiB
```

The machine-readable source for the new resource measurements is
`test-results/resource-report-current-full.json`; these numbers are a local
baseline, not a cross-host performance claim.

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

The root workflows now pin the current coherent vendor revisions: Kogwistar
`428e15de42ed0fd4d64e0f4549403493108afc93`, kg-doc-parser
`0bb8a22792a59211e98120d75915fcf808ac8116`, and the unchanged Obsidian sink
`73541e0f7cfe160639f324d84a181faeb0801835`. The Kogwistar revision is the
exact commit pinned by parser `main` and has the same tree as the latest core
`main` merge. The post-refresh GitHub validation is tracked by the workflows
for root commit `10ce58d`; the PyPy profile remains diagnostic rather than a
production native-extension claim.

The follow-up binary report gives the more precise result: pydantic-core has ten
missing Python ABI symbols (`PyList_GET_ITEM`, `PyList_GET_SIZE`,
`PyList_SET_ITEM`, `PyObject_LengthHint`, `PyPyFrozenSet_Check`,
`PyPySet_Check`, `_PyLong_AsByteArray`, `_PyLong_FromByteArray`,
`_PyLong_NumBits`, and `_Py_Dealloc`), while the independently loaded
Kogwistar extension is missing `PyPyExc_AttributeError`. These are broad
PyO3/PyPy ABI mismatches, not isolated application bugs, so no additional
one-symbol aliases are being added.

Pinning `rpds-py==2026.5.1` does not address this path. The pydantic-core/PyO3
FFI combination needs an upstream-compatible build or an explicitly tested
CI-only source patch before the application profile can pass provider-free
tests. We intentionally do not add another one-symbol workaround until the
audit shows whether the mismatch is narrow or systemic.

The parser checks used by the new root pin are now validated on GitHub by the
current parser CI and PyPy runs. Image promotion still requires the separate
native-extension, storage, and container-health gates.

The current local parser provider-free focused slice is `47 passed, 64
deselected` in 288.69 seconds. Root profile/slot contract tests are `18
passed`; these are CPython checks and do not replace the missing PyPy runtime
evidence.

The persistent WSL PyPy 3.11.13 environment passed the NumPy-free profile
checker and the slot benchmark. The base profile intentionally excludes
optional native dependencies. The shared MCP adapter has since migrated from
the legacy standalone FastMCP package to the official `mcp` SDK, so PyPy and
CPython now use one implementation. The provider-free MCP protocol slice
passed in the current clean Linux PyPy run; native extension, storage, and
production-image support remain experimental.

The same WSL PyPy environment independently passes the six Kogwistar PyPy
contract/ABI-diagnostic tests. That proves the core Python-authority and audit
helpers are runnable under PyPy 3.11; it does not by itself prove the complete
MCP application profile.

## Slots Measurement

The checked-in source is `doc/slots_benchmark_cpython.json`. It compares each
migrated class with an equivalent unslotted baseline at 10,000 instances on
CPython 3.13.3. The primary result is retained-memory reduction measured with
`tracemalloc`; construction speed is deliberately not treated as a universal
improvement.

| Class | Slotted peak bytes | Unslotted peak bytes | Peak reduction | Slotted wall us/instance | Unslotted wall us/instance | Slotted CPU s/10k | Unslotted CPU s/10k |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `CodexBridgeState` | 2,015,104 | 2,415,104 | 16.6% | 4.092 | 5.997 | 0.046875 | 0.062500 |
| `CodexMemoryService` | 736,046 | 1,765,160 | 58.3% | 19.368 | 4.095 | 0.187500 | 0.046875 |
| `ComposeOptions` | 1,685,408 | 2,245,408 | 24.9% | 4.815 | 5.100 | 0.046875 | 0.046875 |
| `ContextMessage` | 725,264 | 1,125,264 | 35.5% | 2.191 | 1.802 | 0.031250 | 0.015625 |
| `DroppedItem` | 725,264 | 1,125,264 | 35.5% | 1.604 | 2.061 | 0.015625 | 0.031250 |
| `LaunchStep` | 725,264 | 1,125,264 | 35.5% | 2.846 | 2.642 | 0.031250 | 0.031250 |
| `LaneMessageLookup` | 1,765,408 | 2,325,408 | 24.1% | 4.094 | 5.573 | 0.046875 | 0.046875 |
| `LaneMessageProjectionRepairResult` | 805,456 | 1,205,312 | 33.2% | 2.901 | 2.091 | 0.031250 | 0.015625 |
| `LaneMessageSendResult` | 805,456 | 1,205,312 | 33.2% | 2.920 | 2.473 | 0.031250 | 0.015625 |
| `LiveTracePrinter` | 494,952 | 1,525,160 | 67.5% | 1.976 | 5.007 | 0.015625 | 0.046875 |
| `LlmWikiIdentity` | 965,328 | 1,365,472 | 29.3% | 2.882 | 3.066 | 0.031250 | 0.031250 |
| `MaintenanceJobExecutionContext` | 1,045,480 | 1,525,480 | 31.5% | 4.131 | 2.370 | 0.046875 | 0.031250 |
| `MaintenanceStrategyRegistry` | 1,045,320 | 2,085,160 | 49.9% | 2.384 | 5.230 | 0.015625 | 0.062500 |
| `MappingAssetResolver` | 1,125,264 | 2,165,160 | 48.0% | 1.510 | 3.751 | 0.015625 | 0.031250 |
| `RetrievalOutcome` | 805,312 | 1,205,456 | 33.2% | 1.718 | 4.111 | 0.015625 | 0.046875 |
| `ReviewQueryService` | 485,264 | 1,525,160 | 68.2% | 1.436 | 3.783 | 0.015625 | 0.046875 |
| `SemanticLensService` | 654,968 | 1,685,160 | 61.1% | 2.219 | 5.720 | 0.031250 | 0.062500 |
| `TuiConfiguration` | 1,445,408 | 1,925,408 | 24.9% | 4.462 | 3.774 | 0.046875 | 0.031250 |
| `WorkbenchInteractionStore` | 485,264 | 1,525,160 | 68.2% | 0.862 | 3.391 | 0.015625 | 0.031250 |

The benchmark used counts of 100 and 10,000 with `warmup_count=0` because this
artifact is a CPython baseline. The peak column is the retained `tracemalloc`
allocation for the batch, including the unslotted instance dictionary; the
wall-time columns are construction time only. Slots reduced memory for every
class in this wave, but construction became slower for `CodexMemoryService`,
`LiveTracePrinter`, and a few other small objects. PyPy must be measured
separately for the available 3.11 or 3.12 profile, with a warm-up population
such as:

```powershell
pypy3 scripts\benchmark_slots.py `
  --warmup-count 10000 --count 1000 --count 10000 `
  --json-out doc\slots_benchmark_pypy312.json
```

### Same-WSL Runtime Comparison

To avoid comparing WSL PyPy with native Windows CPython, the same benchmark was
run inside Ubuntu-24.04/WSL2 using PyPy 3.11.13, CPython 3.13.15, and CPython
3.14.7. The 10,000-instance results below are averages across the benchmarked
classes; negative values mean the slotted construction was faster than its
unslotted equivalent.

| Runtime | Warm-up | Slotted wall change | Slotted CPU change | Per-object allocation bytes |
| --- | ---: | ---: | ---: | --- |
| CPython 3.13.15 WSL | 0 | -5.2% | -5.2% | available; use the JSON for allocation details |
| CPython 3.14.7 WSL | 0 | -11.3% | -11.3% | available; use the JSON for allocation details |
| PyPy 3.11.13 WSL | 1,000 | -7.4% | -6.7% | unavailable; PyPy rejects `sys.getsizeof()` and this build has no `_tracemalloc` |

The CI matrix also uploads per-test JSON reports with wall time, process CPU
time, derived CPU utilization, and resident-memory measurements. Compare those
artifacts only between runtimes on the same operating-system image; CPU
utilization is derived from process CPU time divided by wall time, and RSS is
process-wide.

The PyPy probe lanes additionally upload their JSON compatibility report,
`pip freeze --all`, and `pip check` output even when a crash-sensitive import
fails. This preserves resolver and failure evidence instead of reducing a
blocked run to a single exit code.

These are construction microbenchmarks after the selected warm-up, not an
application throughput claim. PyPy's JIT and object strategies make its
absolute numbers non-comparable to CPython; the useful comparison is the
slotted-versus-unslotted direction within each interpreter. The generated
JSON reports are under `test-results/slots-benchmark-same-host-wsl/` locally,
and the automatic GitHub slot workflow uploads equivalent artifacts for
CPython 3.12/3.13/3.14 and PyPy 3.11.

A fresh same-host rerun with the current reporter produced the table above
across the same 19 classes, including the three lane-messaging DTOs added in
the core slot wave. The authoritative manifest is
`test-results/slots-benchmark-same-host-wsl/manifest.json`; this is evidence of
the current tree, not a release threshold. The earlier table values were
replaced because JIT and host load make this microbenchmark variable.

## PyPy Status

PyPy 3.12 remains experimental and opt-in:

- the required source CI matrix now covers CPython 3.12 and 3.13;
- the core Kogwistar CI and the application workflow now run automatic,
  non-blocking PyPy 3.12 probes; the latest core run built and installed the
  wheel but its native verification / fallback path still failed; both probes
  remain non-required;
- the separate PyPy CI Docker workflow is manual and publishes no production
  release tag;
- no local PyPy 3.12 runtime was available for a native run;
- the local `llm-wiki-pypy311-ci:local` image was built successfully from
  `Dockerfile.pypy-ci` with PyPy 3.11.13 and Rust 1.91.1. Its in-container
  virtual-environment profile passed the interpreter, import-path, and
  NumPy/Chroma exclusion checks;
- the same image completed the provider-free PyPy profile after installing the
  pinned sibling source trees. The image is a CI/diagnostic image only and
  does not contain model weights or production model-serving dependencies;
- the root workflows now reference the merged-main Kogwistar and parser
  revisions; the beta lane applies the temporary
  `rpds-py==2026.5.1` compatibility constraint plus a diagnostic Pydantic pair,
  but the latter still fails in the complete PyPy verification path;
- PyO3/Maturin installed-wheel loading and the pgvector/storage boundary still
  require real PyPy probes.

The current local PyPy 3.11 evidence is:

```text
provider-free parser/core selection: 36 passed, 20 deselected
root PyPy profile and publishing tests: 32 passed
container profile: PASS
```

The long parser/core selection took 860.83 seconds in WSL. It is useful as a
compatibility signal, not as a normal CI timing target. The container workflow
uses `load: true` and `push: false`; no PyPy image is published until the
official-MCP application profile has an independent passing result.

The supported claim is therefore **CPython 3.12/3.13 source compatibility**,
not production PyPy compatibility. The PyPy profile intentionally excludes
NumPy, Chroma, Torch, and other unsupported optional native paths.

## Reproduction Links

- [PyPy support plan](pypy_3_12_support_action_plan.md)
- [PyPy compatibility matrix](pypy_3_12_compatibility_matrix.md)
- [Slots memory-layout plan](python_slots_memory_layout_plan.md)
- [CPython benchmark artifact](slots_benchmark_cpython.json)

The automatic `.github/workflows/slots-benchmark.yml` workflow now repeats the
measurement on CPython 3.12, CPython 3.13, CPython 3.14, and PyPy 3.11,
uploading one JSON artifact per runtime. Its additional same-host job runs
CPython 3.13, CPython 3.14, and PyPy 3.11 sequentially on one Ubuntu runner,
so that comparison is not spread across separate hosted VMs. The PyPy result
is warmed before measurement and is informational rather than a release gate;
it cannot establish native extension or Docker-image compatibility. Local WSL
comparisons must use WSL CPython beside WSL PyPy; native Windows comparisons
must use native Windows interpreters.

The latest root benchmark run is `35525514019` for commit `a4c6e39`; it passed
and produced the runtime artifacts. The local same-host WSL manifest also
contains CPython 3.13, CPython 3.14, and PyPy 3.11 reports with the requested
wall-time, process-CPU, and slotted-versus-unslotted memory measurements.
