# PyPy 3.12 Support Action Plan

## Status

This is an experimental compatibility plan, not a declaration of production
support.

As of 2026-09-19, PyPy 8.0.0 includes a Python 3.12 interpreter at beta
quality. The Python 3.12 milestone remains open, and the v8.0.0 release notes
warn that bugs and packaging gaps remain. Experimental builds and CI probes
can therefore start now, but PyPy 3.12 must not become a required or
production-supported runtime until its interpreter, extension loading, and
dependency ecosystem pass this project's promotion gates.

Primary references:

- https://pypy.org/download.html
- https://doc.pypy.org/release-v8.0.0.html
- https://github.com/pypy/pypy/milestone/23
- https://doc.pypy.org/release-v7.3.20.html
- https://pyo3.rs/main/features.html
- https://github.com/PyO3/maturin

## Goal

Support the Torch-free LLM-Wiki control plane on PyPy implementing Python
3.12 without weakening the existing CPython 3.13 deployment. PyPy support is
an additional runtime profile. It does not replace CPython and does not imply
that GPU inference, vLLM, Torch, or every optional parser dependency runs in
the PyPy process.

The first supported PyPy profile should cover:

- provider-free unit and contract tests;
- the LLM-Wiki CLI and configuration layer;
- REST and MCP serving;
- maintenance orchestration with fake or remote providers;
- PostgreSQL-backed graph operations, if the database driver passes the
  compatibility gate;
- remote HTTP embedding through the existing service boundary;
- archive, projection, and workbench behavior that does not require an
  unsupported native dependency.

The requested target is **NumPy-free and Chroma-free**, with PostgreSQL storage
and remote embeddings. That target is not currently installable: the Python
`pgvector` adapter also requires NumPy. Phase 0 must resolve this dependency
boundary before selecting an executable beta profile. A profile with validated
NumPy support is a separate option, not fulfillment of the NumPy-free target.

The following remain out of scope for the initial profile:

- in-process Torch or Transformers inference;
- vLLM inside the PyPy application process;
- CUDA validation;
- claiming performance improvement before warm JIT benchmarks prove it;
- silently replacing the current CPython application image.

## Current Compatibility Gaps

### Interpreter floor

The root package and parser now declare Python 3.12 as their minimum, matching
Kogwistar and the Obsidian sink. The root Ruff target is also `py312`, and the
required provider-free CI job runs a CPython 3.12/3.13 matrix.

PyPy 3.12 support still requires the new CPython 3.12 leg to pass and the
native dependency profile to be proven. Lowering metadata floors is not itself
evidence that PyPy can install or load the packages.

### Kogwistar Rust extension

Kogwistar is built with Maturin and PyO3. Its Rust workspace currently enables
`extension-module` and `abi3-py312`.

`abi3-py312` identifies a minimum CPython stable-ABI level. It must not be
treated as proof that the wheel loads or behaves correctly on PyPy. PyPy uses
its own compatibility layer for CPython extension modules, and the extension
must be built and tested against the actual target interpreter.

The current Maturin/PyO3 toolchain also needs a live capability check. PyPy
8.0.0 introduces a C object layout intended to support CPython 3.12 limited-ABI
wheels, but its release notes state that import machinery and installer
acceptance for `cp312-abi3` wheels are not yet complete. PyPy is coordinating
with PyO3, but Kogwistar must still build and test against the beta interpreter
directly. PyPy 3.12 remains experimental until the selected Maturin/PyO3/pip
combination accepts it and Kogwistar's extension contract suite passes.

### Native dependency surface

The dependency resolver must be tested rather than inferred. Important native
or interpreter-sensitive boundaries include:

- `pydantic-core`, required by Pydantic v2;
- the Kogwistar PyO3 extension;
- `numpy` and optional Chroma dependencies;
- `pikepdf`, imported by the document parser PDF path;
- `cryptography` through authentication packages;
- PostgreSQL drivers and their binary extras;
- `tiktoken`, required by the current `langchain-openai` embedding adapter even
  when inference runs remotely;
- optional FastMCP and telemetry dependency trees;
- Torch, torchvision, bitsandbytes, and other multimodal packages.

A package installing on CPython 3.12 is not sufficient evidence that it has a
PyPy 3.12 wheel or a viable source build.

### Verified NumPy and Chroma boundary

The repository audit found the following current behavior:

- LLM-Wiki declares `chromadb` only in its `chromadb` and `dev` extras and has
  no direct NumPy import in application-owned source.
- Kogwistar's previous base dependency set declared `numpy>=1.26` even though
  `chromadb` is an optional `chroma` extra. The base dependency has now been
  removed; the lock-style `requirements.txt` remains a full Chroma-oriented
  environment and still contains NumPy by design.
- Kogwistar's previous `engine_core.embedding_factory` imported NumPy
  unconditionally, but used it only to convert, calculate an L2 norm, divide,
  and convert back to Python lists. The factory exposes Chroma's
  embedding-function shape, but those normalization operations do not require
  NumPy.
- Kogwistar's general `utils.embedding_vectors` module already treats NumPy as
  optional and accepts ordinary numeric sequences without it.
- The parser does not directly import NumPy. Its PDF fallback now imports
  `pikepdf` lazily, and the Poetry dependency is excluded on PyPy so the
  text/control-plane profile does not require that native PDF extension. PDF
  fallback remains available on CPython when `pikepdf` is installed.
- Installed `pgvector` distribution metadata declares `numpy`. Kogwistar's
  `postgres_backend.py` imports `pgvector.sqlalchemy.Vector`, rejects a missing
  adapter during construction, and uses it for columns and query parameters.
- LLM-Wiki's `_build_postgres_engine` constructs that Python backend directly.
  Selecting Rust authority alone does not remove the Python adapter dependency.

Removing the embedding factory's NumPy dependency is useful but insufficient
for PostgreSQL. The core normalization change should:

1. implement scaled standard-library L2 normalization, avoiding overflow and
   underflow from directly summing squared inputs;
2. keep NumPy out of mandatory base dependencies;
3. retain NumPy transitively in existing Chroma and Python pgvector extras
   until their respective implementations no longer require it;
4. preserve support for NumPy-like provider results through the existing
   optional `.tolist()` boundary without importing NumPy;
5. add an import and embedding-contract test in an environment where both
   `numpy` and `chromadb` are absent.

Test ordinary outputs against the existing implementation within documented
tolerances. Explicitly test zeros, extreme finite values, and non-finite input
handling; do not silently change persisted embedding-profile semantics.

This change belongs in Kogwistar first. LLM-Wiki must pin accepted upstream
changes before claiming the corresponding compatibility.

Implementation checkpoint: Kogwistar's embedding factory now uses scaled
standard-library normalization and no longer imports NumPy at module load time.
Its focused tests pass with NumPy blocked. The requested full profile remains
blocked until the PostgreSQL storage route is resolved and tested, because the
current Python `pgvector` dependency still requires NumPy.

### Container and CI assumptions

The application Dockerfile remains pinned to `python:3.13-slim` for production.
GitHub's required provider-free job now tests CPython 3.12 and 3.13; Docker and
release artifacts remain CPython 3.13 until the PyPy gates pass.

## Support Levels

| Level | Runtime | Meaning |
| --- | --- | --- |
| Production | CPython 3.13 | Current application, CI, and Docker baseline. |
| Prerequisite | CPython 3.12 | Proves source and dependency-floor compatibility. |
| Experimental | PyPy 3.12 beta | Run provider-free and remote-service probes now using a pinned beta build. |
| Unsupported | PyPy in-process GPU | Torch, vLLM, and CUDA remain separate services. |

No level changes automatically. Promotion requires the explicit gates below.

## Repository Sequence

1. Update Kogwistar first because its PyO3 extension is the hardest runtime
   boundary.
2. Update `kg-doc-parser` after Kogwistar has a tested PyPy-compatible path.
3. Update `kogwistar-obsidian-sink` against the accepted Kogwistar revision.
4. Update LLM-Wiki last and pin all accepted dependency revisions.

Each repository must pass its own CPython CI before downstream pins change.
PyPy jobs may begin as experimental, but a repository must not advertise PyPy
support while its required upstream package remains unproven.

## Phase 0: Freeze the Baseline

- Record the CPython 3.13 provider-free test count, duration, peak RSS, and
  startup time.
- Record package versions and the current Kogwistar Rust feature set.
- Add a small runtime report containing `sys.version`,
  `sys.implementation.name`, architecture, wheel tags, and extension mode.
- Keep all real-provider, Docker, GPU, and model-load tests marked `manual`
  and/or `slow`.
- Do not change release metadata in this phase.

### Decide the PostgreSQL route

Capture a fresh resolver report for each candidate on the actual target
interpreter, including transitive dependencies, versions, artifacts, checksums,
and `pip check`. Installed workstation metadata is audit evidence, not proof
of what a fresh PyPy installation resolves.

| Candidate | NumPy-free | Work and decision gate |
| --- | --- | --- |
| Existing Python pgvector backend | No | Probe NumPy and pgvector on PyPy; retain as an alternative requiring a scope decision. |
| Existing Rust service as storage boundary | Potentially | Audit API coverage, then implement missing application wiring and verify transactions, ACLs, provenance, and vector retrieval. |
| Python adapter without NumPy | Potentially | Consider only after measuring gaps in existing primitives; requires codec, SQL parameter, dimension, distance, and transaction parity tests. |

Prefer reuse of existing Kogwistar capabilities. Do not create a second graph
implementation or assume the Rust service is a drop-in replacement. If no
NumPy-free storage route passes, report that profile as blocked while continuing
interpreter and dependency probes. Do not publish it as supported.

## Phase 1: Make Python 3.12 a Valid Source Target

- Run the root, parser, sink, and Kogwistar provider-free suites on CPython
  3.12.
- Audit Python 3.13-only syntax and standard-library APIs.
- Set Ruff or Pyright's target version to Python 3.12 for the compatibility
  branch.
- Lower Python floors on the compatibility branch to permit real installation
  tests; merge those metadata changes only after the selected package profiles
  install and pass on CPython 3.12 and 3.13.
- Change `kg-doc-parser` from `^3.13` only after its PDF and provider paths are
  validated on 3.12.
- Keep the production Docker image on CPython 3.13 during this phase.

Acceptance gate:

- CPython 3.12 and 3.13 run the same deterministic CI selection.
- Serialization fixtures and stable IDs are identical across both versions.
- No compatibility shim changes graph, provenance, budget, or namespace
  semantics.

## Phase 2: Prove the Kogwistar Native Boundary

Test these implementation strategies in order:

1. Build the existing PyO3 extension directly with the target PyPy 3.12
   interpreter and run the complete Rust/Python bridge contract suite.
2. If direct extension loading is unavailable, use an existing pure-Python
   Kogwistar authority mode only where parity tests already prove it correct.
3. If in-process native support remains unsuitable, evaluate the existing Rust
   service against the Phase 0 contract checklist. Application integration and
   packaging changes are required before it can substitute for local graph
   objects or eliminate the local extension requirement.

Required checks:

- canonical JSON and stable-ID parity;
- runtime routing, retries, budgets, joins, and suspension;
- exception type and message compatibility;
- no silent fallback when the selected authority is configured as required.

Apply boundary checks according to the selected route:

- embedded-native mode: import and capability reporting for `kogwistar._rust`,
  SQLite and PostgreSQL store operations, and wheel tags/Maturin metadata for
  the target interpreter;
- service mode: protocol version negotiation, authentication, reconnect and
  outage behavior, transaction results, ACL/provenance parity, vector
  retrieval parity, and an application integration test proving LLM-Wiki uses
  the service rather than constructing the local Python backend.

Do not treat the service option as a drop-in fallback. The selected route and
its evidence must be recorded in the compatibility report.

Do not publish a CPython `abi3` wheel as PyPy-compatible solely because its
filename appears version-independent.

## Phase 3: Resolve the Dependency Matrix

Create a checked-in compatibility report with one row per required dependency:

| Dependency | Required path | PyPy wheel | Source build | Decision |
| --- | --- | --- | --- | --- |
| Pydantic/pydantic-core | all profiles | probe | probe | blocking |
| Kogwistar PyO3 extension | all graph profiles | probe | probe | blocking |
| FastMCP | MCP profile | probe | probe | blocking for MCP |
| SQLAlchemy | PostgreSQL profile | probe | probe | required |
| PostgreSQL driver | PostgreSQL profile | probe | probe | required |
| OpenTelemetry | telemetry profile | probe | probe | optional |
| pikepdf/pdf2image | PDF parser | probe | probe | isolate if unavailable |
| NumPy/pgvector | current Python PostgreSQL backend | probe | probe | blocking for NumPy-free target |
| Chroma | optional Chroma backend | not targeted | not targeted | excluded initially |
| tiktoken | current OpenAI embedding adapter | probe | probe | blocking for that adapter |
| Torch stack | in-process multimodal | not targeted | not targeted | excluded |

If `pikepdf` or another PDF dependency blocks installation, split parser core
and PDF extras instead of making the full LLM-Wiki control plane CPython-only.
The parser must fail with a clear capability error when an unavailable media
path is selected.

PDF dependencies remain mandatory for CPython parser installs, while the
PyPy profile excludes `pikepdf` through an implementation marker and loads it
lazily. Merely omitting an extra cannot remove a dependency. Split packaging
and verify lazy imports first, with text-only startup tests and separate PDF
regression coverage. Likewise, remote inference does not prove the client
SDK's native dependencies work.
Reuse an existing HTTP adapter where its contract fits; validate any provider
change for token counting, dimensions, ordering, retries, and profile identity.

If a PostgreSQL binary extra is unavailable, test the driver's supported pure
Python path. Do not silently switch drivers without transaction, timeout, and
type-adaptation parity tests.

### Initial PyPy install profile

The beta lane must install a deliberately bounded profile rather than the
current `dev` extra:

- Kogwistar base plus the storage route accepted in Phase 0 and necessary
  serving dependencies; the current `pgvector` extra cannot be used for the
  NumPy-free profile;
- parser core with incompatible PDF/media extras omitted where necessary;
- Obsidian sink only after its Kogwistar pin passes;
- LLM-Wiki test, agent, and optional telemetry dependencies that resolve on
  PyPy;
- no LLM-Wiki `chromadb`, `dev`, `multimodal`, or embedding-service extra;
- no Kogwistar `chroma`, `full`, browser, or in-process model extra.

For the NumPy-free profile, CI must assert that `importlib.util.find_spec("numpy")` and
`importlib.util.find_spec("chromadb")` both return `None`. This makes accidental
dependency leakage a test failure rather than an undocumented profile change.

## Phase 4: Add an Experimental PyPy CI Lane

The repository now provides a manual probe at
`.github/workflows/pypy-beta.yml`. Run it from **Actions -> Experimental PyPy
3.12 beta** for the initial interpreter and dependency-boundary check. The
reusable local checker is:

```text
python scripts/check_pypy_profile.py --json
```

After a genuinely installed, non-editable application profile exists, run the
checker with `--installed-only --require-import kogwistar
--require-import kogwistar_llm_wiki`; it rejects checkout imports and requires
both packages to resolve from the environment.
The workflow is manual and non-required by design. It records the exact
interpreter and wheel-tag diagnostics, but does not claim production support.
Set its `installed_wheel` input to `true` for the slower non-editable wheel
probe; that path installs into a temporary environment outside the checkout
and runs the installed-only origin checks.
Because `actions/setup-python` currently resolves the PyPy selector through its
toolcache manifest rather than accepting a repository-local archive checksum,
this first lane is a capability probe, not yet a reproducibly pinned release
lane. The release-quality lane below remains a follow-up gate: it must pin the
official archive URL and checksum before it can be required or used for a
published image.

The current lane intentionally uses the `pypy3.12` selector as a capability
probe. Before making it required or using it for a published image, replace
that selector with a reproducibly pinned PyPy 8.0.0 Python 3.12 beta artifact.
If `actions/setup-python` does not yet expose that build, download the official
beta artifact, verify its published checksum, and cache only the verified
archive. Do not use an unpinned moving nightly for required CI.

Start with interpreter/build/dependency probes. Enable full application tests
after the selected upstream revisions and storage route are installable.

The bounded source profile is checked in at
`requirements/pypy-3.12-beta.txt`. It is installed only after the four local
packages are present with `--no-deps`; this prevents the resolver from pulling
the full CPython/dev extras or silently adding NumPy, Chroma, pgvector, Torch,
or pikepdf. The current evidence for each boundary is tracked in
`doc/pypy_3_12_compatibility_matrix.md`.

The lane should:

- use a pinned PyPy beta release, checksum, and architecture;
- print interpreter and wheel-tag diagnostics;
- build/install Kogwistar using the selected native strategy;
- install the bounded PostgreSQL/remote-embedding profile without NumPy,
  Chroma, Torch, or CUDA extras;
- assert that neither `numpy` nor `chromadb` is importable;
- run `python -m ruff` under the normal lint interpreter, not require Ruff to
  execute on PyPy;
- run `python -m pytest -m "ci and not ci_full and not slow and not manual and
  not llm_real and not longrun and not requires_ollama"`;
- use `-p no:cacheprovider` in this repository's known Windows/cache-sensitive
  environments;
- publish durations and peak memory;
- never consume real credentials or production data.

An experimental CI base image is available through the manual
`.github/workflows/publish-pypy-ci-dockerhub.yml` workflow. It uses
`Dockerfile.pypy-ci`, requires a versioned archive URL plus SHA-256, and is
published separately as `kogwistar-llm-wiki-pypy-ci:pypy3.12-beta-<build>`.
This is deliberately not an application release image: the workflow smoke
tests the interpreter/toolchain and does not change production tags. The
PyPy archive checksum and exact build remain explicit workflow inputs until a
stable PyPy 3.12 release is selected.

Add an installed-wheel smoke suite in a fresh environment outside the checkout,
with no editable installs, source-path injection, or inherited `PYTHONPATH`.
The current repository `tests/conftest.py` adds source paths, so it cannot prove
wheel contents or installed imports are correct. Verify module origins,
extension loading, packaged resources, CLI help, and fake MCP/REST requests.

Use an explicit profile test selection and report collected, passed, skipped,
and deselected counts. Do not make Chroma failures disappear through broad
skips. Run PostgreSQL correctness fixtures in a separate deterministic service
job; keep real model tests manual and lengthy/Docker cases slow. Include test
durations in each run.

Start as `continue-on-error` with an issue link and expiry date. Remove
`continue-on-error` before declaring support.

## Phase 5: Packaging and Container Profile

- Add PyPy classifiers only after required CI is green.
- Publish interpreter-appropriate wheels; do not relabel CPython wheels.
- Add a separate PyPy application image, for example
  `kogwistar-llm-wiki:pypy3.12-<version>`.
- Keep the normal `v*`, minor, and `latest` tags on the existing CPython image
  unless a later release decision changes the default.
- Keep vLLM and custom embedding services as separate CPython/CUDA images.
- Verify the PyPy image contains no model weights, Rust compiler, Maturin, or
  build cache.
- Add a container health and `llm-wiki --help` smoke test.
- Promote only the exact wheel and image digests that passed the beta gates;
  record those digests with the interpreter, dependency lock, and upstream
  revision set. Rebuilding after validation requires a new validation run.

## Phase 6: Correctness and Performance Evaluation

PyPy's JIT needs warm-up, so one-shot command latency is not a sufficient
benchmark.

Measure at minimum:

- cold CLI startup;
- REST/MCP readiness time;
- first request and steady-state request latency;
- maintenance-job throughput after warm-up;
- peak and steady RSS;
- garbage-collection pause distribution;
- serialization and validation throughput;
- Rust bridge call overhead;
- 1, 10, 100, and 1,000 operation batches.

Compare CPython 3.13, CPython 3.12, and PyPy 3.12 using the same fixture and
dependency profile. Report regressions as well as improvements. PyPy is not
promoted if its memory use or native bridge overhead is materially worse for
the service workload.

## Test Plan

- Import every public root package under PyPy.
- Run package metadata and CLI startup tests.
- Run stable-ID, canonical JSON, and provenance fixtures across interpreters.
- Run archive round trips across interpreters.
- Run fake-provider parser and maintenance workflows.
- Run duplicate delivery, retry, budget, and suspension tests.
- Run REST and MCP fake-client integration tests.
- Run PostgreSQL transaction and startup migration tests when its driver is
  available.
- Run Kogwistar's standard-library L2-normalization tests for zero vectors,
  ordinary vectors, extreme finite magnitudes, and provider values exposing
  `.tolist()` without importing NumPy.
- Assert that selecting the Chroma backend without its extra fails with the
  existing actionable capability error, not an import-time crash.
- Verify remote embedding payloads and 1,024/2,048 dimension validation.
- Verify Pydantic model dumps and JSON schemas are byte-stable where contracts
  require stability.
- Mark real provider, Docker, GPU, and long-running tests appropriately.

## Beta Acceptance Gates

A beta artifact is eligible only after its pinned interpreter and dependency
profile install cleanly, required profile tests pass without allowed failures,
installed-wheel checks pass, and storage/provenance/ACL contracts are verified.
The artifact must state its exclusions and measured performance. A successful
experimental CI job does not automatically publish or change production tags.

For the requested NumPy-free artifact, both dependency metadata and runtime
import checks must prove NumPy and Chroma are absent. No claimed storage or
provider path may require them indirectly.

## Production Promotion Gates

PyPy 3.12 becomes production-supported only when all of the following are
true:

- a stable PyPy 3.12 release is pinned and the beta acceptance gates pass;
- CPython 3.12 compatibility is already required in CI;
- all mandatory dependencies resolve from trusted sources;
- the supported PyPy installation contains neither NumPy nor Chroma, and its
  package/import guard is required in CI;
- the Kogwistar native strategy passes parity and transaction tests;
- provider-free PyPy CI is required and green;
- MCP/REST startup and health checks pass;
- no graph, provenance, ACL, namespace, or maintenance invariant differs;
- performance and memory reports are reviewed;
- documentation clearly lists excluded optional features;
- release artifacts are distinguishable from CPython artifacts.

The beta artifact must also be traceable to the exact validated wheel/image
digest and dependency report. A source commit or version tag alone does not
prove that the released artifact matches the tested build.

## Review Follow-up Checklist

- [ ] Resolve Python pgvector's transitive NumPy requirement before selecting
  the initial storage profile.
- [ ] Prove the selected Rust/Python boundary with application integration tests.
- [ ] Replace factory normalization with a numerically tested implementation.
- [ ] Validate client dependencies including tiktoken and pydantic-core.
- [ ] Split parser media dependencies if the text-only profile needs it.
- [ ] Prove the new CPython 3.12 CI leg and serialization parity before
  treating the lowered Python floors as stable.
- [ ] Add clean installed-wheel tests outside repository import overrides.
- [ ] Pin merged Kogwistar, parser, and sink revisions in LLM-Wiki.
- [ ] Publish resolver, test-duration, correctness, and memory reports.
- [ ] Apply beta acceptance and production promotion as separate decisions.

## Explicit Non-Goals

- No fake PyPy 3.12 support based only on source compatibility.
- No disabling Kogwistar invariants to make a native bridge pass.
- No in-process GPU stack requirement.
- No automatic switch of the production Docker image.
- No claim that `abi3-py312` alone provides PyPy compatibility.
- No release tag until the compatibility matrix and CI gates are complete.
