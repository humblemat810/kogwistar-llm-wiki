# PyPy 3.12 Compatibility Matrix

This is the evidence ledger for the experimental NumPy-free, Chroma-free
PyPy 3.12 profile. It is not a production-support declaration. The automatic,
non-blocking core job in `kogwistar/.github/workflows/ci.yml` and the manual
application workflow in `.github/workflows/pypy-beta.yml` must update the probe
result and exact dependency/interpreter evidence before any row is promoted.

| Boundary | Profile requirement | Current state | Evidence or blocking condition |
| --- | --- | --- | --- |
| PyPy interpreter | Python 3.12 beta | Probe only | `pypy-beta.yml` downloads the official moving `nightly/py3.12` archive; no local PyPy 3.12 runtime is installed. |
| Kogwistar base | Import and Rust bridge parity | Blocked | Automatic core run `35454026253` built a real PyPy wheel successfully, but native verification and the Python-authority fallback still failed; all required CPython, Rust, and native-wheel jobs passed. A deeper PyO3/PyPy native-import fix is still required. |
| Kogwistar NumPy boundary | Base import without NumPy | Feature revision ready, automatic beta probe | Core commit `121314be28f300eca0cc55d87a83f40c302b81af` retains the base NumPy removal and adds downloadable native-import diagnostics; the PyPy-only `rpds-py==2026.5.1` workaround remains isolated to CI, and the CPython-only ABI3 feature remains out of the beta build. |
| Parser text path | Import and fake-provider tests | Probe | `pikepdf` is excluded on PyPy and loaded lazily; PDF splitting remains capability-gated. |
| FastMCP | MCP contract | Probe | Included in the bounded profile; requires the PyPy runner and native dependency resolution. |
| PostgreSQL driver | Transaction/ACL/provenance parity | Probe | `psycopg` is included without the binary extra; pgvector is intentionally excluded. |
| Python pgvector | NumPy-free vector storage | Blocked | The current pgvector path requires NumPy; no replacement adapter is claimed. |
| Chroma | Optional backend | Excluded | Must not be installed or importable in this profile. |
| Torch/vLLM/CUDA | In-process inference | Excluded | Inference remains a separate CPython/CUDA service. |
| LLM-Wiki application | Provider-free control plane | Probe | The workflow installs local packages and runs the filtered CI marker when the interpreter is available. |

## Promotion Rules

- A failed or unavailable PyPy probe keeps the profile experimental; it does
  not alter CPython Docker or release tags.
- The core revision, parser revision, sink revision, interpreter artifact, and
  dependency resolution must be recorded together.
- A successful import probe is not enough: Rust bridge, storage, namespace,
  provenance, MCP, and provider-free test parity are required.
- No NumPy-free PostgreSQL vector claim is allowed until the pgvector boundary
  is replaced or independently proven not to require NumPy.
