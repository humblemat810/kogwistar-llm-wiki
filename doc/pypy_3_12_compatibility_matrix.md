# PyPy 3.12 Compatibility Matrix

This is the evidence ledger for the experimental NumPy-free, Chroma-free
PyPy 3.12 profile. It is not a production-support declaration. The manual
workflow in `.github/workflows/pypy-beta.yml` must update the probe result and
the exact dependency/interpreter evidence before any row is promoted.

| Boundary | Profile requirement | Current state | Evidence or blocking condition |
| --- | --- | --- | --- |
| PyPy interpreter | Python 3.12 beta | Probe only | `pypy-beta.yml` uses the `pypy3.12` selector; no local PyPy 3.12 runtime is installed. |
| Kogwistar base | Import and Rust bridge parity | Blocked | Requires a PyPy-native build/test of the pinned PyO3 extension. |
| Kogwistar NumPy boundary | Base import without NumPy | Feature revision ready, CI probe pending | Core commit `f5bf522e0ccd9ed35ea3b3043f8f8fc5712fc6b8` removes the base NumPy dependency, refreshes SQLite reads after external commits, and raises the FastMCP/MCP security floors; the feature branch is remotely available, but the PyPy workflow is manual. |
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
