# PyPy 3.12 Compatibility Matrix

This is the evidence ledger for the experimental NumPy-free, Chroma-free
PyPy 3.12 profile. It is not a production-support declaration. The automatic,
non-blocking core job in `kogwistar/.github/workflows/ci.yml` and the automatic,
non-blocking application workflow in `.github/workflows/pypy-beta.yml` update
the probe result and exact dependency/interpreter evidence before any row is
promoted.

## Experimental PyPy 3.11

The separate `.github/workflows/pypy-311-experimental.yml` lane uses the
official PyPy 3.11 v7.3.20 release archive and verifies its checksum. It is a
Python-authority, NumPy-free control-plane probe only. It does not establish
native Kogwistar extension compatibility and does not publish a PyPy image.

The provider-free control-plane profile is green on Linux/WSL and in the
PyPy CI container. PyPy 3.11.13 imports the application-owned control-plane
modules with NumPy and Chroma excluded, and the focused parser/core selection
passes. The former FastMCP dependency was removed from the candidate
implementation and the shared MCP adapter now uses the official `mcp` SDK.
The provider-free MCP protocol slice also passed in the current clean Linux
run (`35525513959`); native extension, storage, and production-image support
remain experimental, so no production image is published from this result
alone.

The profile is currently validated on Linux/WSL only. A native Windows PyPy
3.11 venv was provisioned successfully, but the bounded dependency resolver
cannot install `mcp`: its Windows dependency path requires `pywin32`, and no
matching PyPy distribution is available. This is a packaging-platform blocker,
not evidence that native Windows PyPy is equivalent to CPython. Do not loosen
the profile or substitute `pywin32-ctypes` without an MCP compatibility test.
The same native venv does pass the dependency-light profile checker with both
NumPy and Chroma absent, plus the Kogwistar PyPy contract and high-cardinality
slot tests. The application-owned slot benchmark cannot yet run there because
the remaining parser path needs `langchain-core`, whose PyYAML dependency does
not currently build/install reliably on this Windows PyPy environment. These
results are useful interpreter/core evidence only and do not authorize an
application image.

| Boundary | Profile requirement | Current state | Evidence or blocking condition |
| --- | --- | --- | --- |
| PyPy interpreter | Python 3.11 release | Automatic experimental probe | v7.3.20 archive is pinned and checksum-verified. |
| Kogwistar Python authority | Provider-free imports/tests | Probe | Runs with `KOGWISTAR_IMPL_MODE=python`; native extension is a separate gate. |
| NumPy/Chroma boundary | Neither importable | Excluded | Dedicated 3.11 requirement profile omits both and the profile checker rejects either. |
| Official MCP SDK on PyPy 3.11 | MCP serving | Provider-free protocol gate passed; production experimental | The shared adapter imports the official `mcp` package instead of standalone FastMCP. Run `35525513959` passed the provider-free application and MCP selection; native extension and production-image gates remain separate. |
| Native Windows dependency resolution | Full PyPy 3.11 profile | Blocked, Windows-only | `mcp` declares `pywin32` only for `sys_platform == 'win32'`, but pip reports no matching PyPy distribution. This does not affect Linux/WSL dependency resolution or Linux container eligibility. |
| Native Windows dependency-light checks | Interpreter/core evidence | Passed | PyPy 3.11.13 profile checker reports NumPy/Chroma absent; Kogwistar contract and high-cardinality slot tests pass. Application imports and benchmark remain outside this result. |
| Native Windows application benchmark | Slots/runtime comparison | Blocked | The parser import path needs `langchain-core`; its PyYAML build dependency did not complete reliably in this PyPy venv. |
| Linux container image | PyPy 3.11 provider-free smoke | Passed, full runtime not claimed | The pinned `Dockerfile.pypy-ci` image builds, creates an in-container virtual environment, and runs the full provider-free application selection including official-MCP gateway tests. MCP startup is still gated on the native-extension, storage, and container-health gates. |
| Native extension and image | Build, loader, storage parity | Not claimed | Requires a separate passing native ABI and packaging gate before publication. |

| Boundary | Profile requirement | Current state | Evidence or blocking condition |
| --- | --- | --- | --- |
| PyPy interpreter | Python 3.12 beta | Probe only | `pypy-beta.yml` defaults to the immutable PyPy v8.0.0 `pypy3.12-v8.0.0-linux64.tar.gz` release and verifies its published SHA-256; manual dispatch can select another versioned archive. No local PyPy 3.12 runtime is installed. |
| Kogwistar base | Import and Rust bridge parity | Blocked / independently probed | The native `_rust` extension builds, but its binary/loader gate reports missing `PyPyExc_AttributeError`. The full package import is also masked by the separate pydantic-core failure. |
| Pydantic core | Application model validation | Blocked by binary ABI | `pydantic==2.12.5` / `pydantic-core==2.41.5` builds, but the binary audit reports 10 missing Python ABI symbols (`PyList_GET_ITEM`, `PyList_GET_SIZE`, `PyList_SET_ITEM`, `PyObject_LengthHint`, `PyPyFrozenSet_Check`, `PyPySet_Check`, `_PyLong_AsByteArray`, `_PyLong_FromByteArray`, `_PyLong_NumBits`, `_Py_Dealloc`); `RTLD_NOW` confirms the loader failure. This remains separate from the `rpds-py==2026.5.1` workaround. |
| Kogwistar NumPy boundary | Base import without NumPy | Feature fix validated locally, automatic beta probe | The pinned core revision `428e15de42ed0fd4d64e0f4549403493108afc93` is the exact Kogwistar revision required by parser `main` and is included in the latest core `main` merge. It retains the NumPy-free base profile; the PyPy-only `rpds-py==2026.5.1` workaround remains isolated to CI, and the CPython-only ABI3 feature remains out of the beta build. |
| Parser text path | Import and fake-provider tests | Probe | `pikepdf` is excluded on PyPy and loaded lazily; PDF splitting remains capability-gated. |
| Official MCP SDK | MCP contract | Experimental | The shared adapter is used for CPython and PyPy; the parent branch must pass the same protocol contract on PyPy before promotion. |
| PostgreSQL driver | Transaction/ACL/provenance parity | Probe | `psycopg` is included without the binary extra; pgvector is intentionally excluded. |
| Python pgvector | NumPy-free vector storage | Blocked | The current pgvector path requires NumPy; no replacement adapter is claimed. |
| Chroma | Optional backend | Excluded | Must not be installed or importable in this profile. |
| Torch/vLLM/CUDA | In-process inference | Excluded | Inference remains a separate CPython/CUDA service. |
| LLM-Wiki application | Provider-free control plane and MCP adapter | Passed; production runtime pending | The workflow installs local packages and runs the filtered CI marker with the shared official-MCP adapter. Native extension, storage, and production-image compatibility remain unclaimed. |

## Promotion Rules

- A failed or unavailable PyPy probe keeps the profile experimental; it does
  not alter CPython Docker or release tags.
- The core revision, parser revision, sink revision, interpreter artifact, and
  dependency resolution must be recorded together.
- A successful import probe is not enough: Rust bridge, storage, namespace,
  provenance, MCP, and provider-free test parity are required.
- No NumPy-free PostgreSQL vector claim is allowed until the pgvector boundary
  is replaced or independently proven not to require NumPy.
