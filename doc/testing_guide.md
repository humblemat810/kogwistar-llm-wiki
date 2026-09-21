# Testing Guide

This guide captures local test-running pitfalls that have already cost time.
Please update it when a failure mode repeats.

## Pytest Cache On Windows

If a pytest command reaches `100% passed` and then hangs until the command
timeout, or if collection fails on `pytest-cache-files-*` directories, check the
pytest cache directory before investigating application logic.

Treat that pattern as a cache-shutdown problem first, not as a product
regression. On this Windows workspace, the test process has repeatedly reached
`100% passed` and only hung while pytest was shutting down its cache provider.

Observed local failure mode:

- `kogwistar/.pytest_cache` existed but was not writable by the test process.
- Top-level generated `kogwistar/pytest-cache-files-*` directories were picked
  up by collection and failed with `WinError 5 Access is denied`.
- `-o cache_dir=C:\tmp\...` also hung because this process could not create
  directories under `C:\tmp`.
- Disabling the cache with `-p no:cacheprovider` made tests exit cleanly, but
  the better default is to use a writable repo-local cache directory.

Current mitigation:

- Vendored `kogwistar/pytest.ini` sets `cache_dir = .pytest-local-cache`.
- Vendored `kogwistar/pytest.ini` excludes `pytest-cache-files-*` from
  recursive collection.
- `.pytest-local-cache` is ignored by the vendored repo's `.gitignore`.

Recommended commands:

```powershell
# Default PR CI slice; includes tests auto-marked as `ci` and prints the
# slowest individual tests for timing visibility.
.\.venv\Scripts\python.exe -m pytest tests -q -m "ci and not ci_full and not slow and not manual and not llm_real and not longrun and not requires_ollama" `
  --durations=25 --durations-min=0 -p no:cacheprovider

# Normal run; uses the repo-local cache configured by pytest.ini.
.\.venv\Scripts\python.exe -m pytest kogwistar/tests/core/test_job_queue_subsystem.py -q

# If cache permissions are suspect and you only need a signal, disable cache.
.\.venv\Scripts\python.exe -m pytest <test-target> -q -p no:cacheprovider
```

## GitHub CI Boundary

`.github/workflows/ci.yml` checks out the application together with pinned
commits of the `kogwistar`, `kg-doc-parser`, and `kogwistar-obsidian-sink`
sibling repositories. Its default Python job runs the `ci` marker while
excluding `ci_full`; tests that use real providers are marked `manual`, and
intentionally long tests are marked `slow`. The workflow runs Ruff and the
vendored Rust workspace checks separately. Docker, live databases, Ollama,
real LLM credentials, and GPU model tests remain opt-in.

The Python job prints the slowest 25 tests using `--durations`, so a growing
CI runtime is visible in the job log rather than hidden behind one total.

### Per-Test Resource Reports

The CI Python matrix also enables the opt-in resource reporter.  It prints
average wall time, process CPU time, CPU utilization (`CPU time / wall time`),
current RSS, and best-effort RSS delta, then uploads one JSON artifact per
interpreter.  Each JSON file also contains one record per test.  The report is
diagnostic only: shared-runner load and JIT warm-up make absolute timings
non-gating. GitHub Actions also renders the aggregate table in the job Step
Summary through `scripts/summarize_resource_report.py`; the JSON artifact
remains the authoritative per-test detail.

The default report intentionally does not enable `tracemalloc`, so CI does not
pay a large profiler slowdown.  For a focused allocation investigation, add
`--resource-report-tracemalloc`; its `average_traced_peak_bytes` value is more
allocation-specific but can substantially increase runtime and is unavailable
on some PyPy builds.

Enable the same report locally:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q `
  -m "ci and not ci_full and not slow and not manual and not llm_real and not longrun and not requires_ollama" `
  --resource-report `
  --resource-report-json test-results\resource-report-cpython.json `
  -p no:cacheprovider
```

For fair interpreter comparisons, run the command inside the same operating
system environment for every runtime.  In particular, compare WSL PyPy with
WSL CPython, not WSL PyPy with native Windows CPython.  Treat
`average_rss_after_bytes` as the cross-runtime memory signal; RSS is
process-wide and can be affected by allocator and JIT behavior.  Use traced
peak bytes only when the report explicitly says `tracemalloc_enabled`.

For a same-host slot comparison, run every interpreter sequentially through
the runner. The runner writes one report per interpreter plus a manifest and
rejects mixed host environments. On WSL, provide WSL CPython 3.13/3.14 and
WSL PyPy 3.11 paths; on native Windows, provide native Windows paths instead:

```powershell
.\.venv\Scripts\python.exe scripts\run_same_host_benchmarks.py `
  --runtime "cpython313|C:\\Python313\\python.exe|0" `
  --runtime "cpython314|C:\\Python314\\python.exe|0" `
  --runtime "pypy311|C:\\pypy311\\pypy3.exe|1000" `
  --count 1000 --count 10000 `
  --output-dir test-results\slots-benchmark-same-host
```

From WSL, use Linux interpreter paths and run the same command in the WSL
shell, for example:

```bash
python3.13 scripts/run_same_host_benchmarks.py \
  --runtime "cpython313|$(command -v python3.13)|0" \
  --runtime "cpython314|$(command -v python3.14)|0" \
  --runtime "pypy311|$HOME/.local/pypy311/bin/pypy3|1000" \
  --count 1000 --count 10000 \
  --output-dir test-results/slots-benchmark-same-host
```

The WSL PyPy path may differ; it must resolve to a PyPy binary inside WSL,
not a Windows `pypy.exe`. Likewise, a native Windows run must not mix in a
WSL interpreter. The full PyPy application profile is currently Linux/WSL-only
because MCP has no matching Windows PyPy `pywin32` distribution; a native
Windows PyPy venv can still be used for interpreter-only or dependency-light
slot probes.

The automatic slot workflow also has a non-required same-host job. It runs
CPython 3.13, CPython 3.14, and PyPy 3.11 sequentially on one Ubuntu runner;
the separate matrix remains useful for per-runtime trend artifacts.

The PyPy compatibility workflows upload `pypy*-profile-evidence` artifacts even
when a crash-sensitive import gate fails. They contain the machine-readable
profile result, `pip freeze --all`, and `pip check` output. The optional
installed-wheel PyPy 3.12 run uploads a separate installed-profile artifact;
these reports are evidence only and do not override a failed compatibility gate.

The latest native-Windows provider-free baseline used this reporter with
`712 passed, 4 skipped, 114 deselected` in 17:09. Its aggregate report was
`1432.592 ms` average wall time, `1368.846 ms` average process CPU time,
`81.9%` average CPU utilization, `1454.17 MiB` average RSS after each test,
and `1851.8 KiB` average RSS delta. These values are a machine-specific
baseline, not a cross-runtime performance claim; the full JSON is generated
as `test-results/resource-report-local-full.json`.

To compare several local interpreter reports after running the same selected
pytest slice under each interpreter, use the comparison helper. The labels are
only display names; each report still retains its exact executable and host
metadata:

```powershell
.\.venv\Scripts\python.exe scripts\compare_resource_reports.py `
  --report "cpython313=test-results\resource-report-cpython313.json" `
  --report "pypy311=test-results\resource-report-pypy311.json"
```

To run that same selected slice under each interpreter and create the reports
in one step, use the same-host runner. Repeat `--pytest-arg` once per pytest
argument; the default target is the full `tests` path with cache disabled:

```powershell
.\.venv\Scripts\python.exe scripts\run_same_host_resource_reports.py `
  --runtime "cpython313=C:\Python313\python.exe" `
  --runtime "pypy311=C:\pypy311\pypy3.exe" `
  --pytest-arg "tests" `
  --pytest-arg "-q" `
  --pytest-arg "-p" `
  --pytest-arg "no:cacheprovider" `
  --pytest-arg "-m" `
  --pytest-arg "ci and not ci_full and not slow and not manual and not llm_real" `
  --output-dir test-results\resource-reports-same-host
```

It writes one JSON file per interpreter, `comparison.md`, and a manifest with
the exact command and exit code. A PyPy runtime that cannot import the selected
application profile is reported as a failed runtime rather than being silently
omitted from the comparison.

The helper rejects reports from different host environments, such as native
Windows and WSL. It compares aggregate wall/CPU/RSS values and leaves the
per-test records in the original JSON files. GitHub CI runs the same
aggregation for its CPython matrix in a non-gating job summary; PyPy probe
jobs publish their own report and summary because they are intentionally
experimental and may not complete the application test slice.

## Debug Run Mode

The llm-wiki CLI can write a debug log, JSONL trace, and sqlite statistics file
for a single ingest run. Point `--debug-run-dir` at an empty directory:

```powershell
.\.venv\Scripts\python.exe -m kogwistar_llm_wiki demo `
  --workspace demo `
  --source .\docs\sample.md `
  --vault .\tests\_tmp\vault `
  --debug-run-dir .\tests\_tmp\llm-wiki-debug
```

The directory will receive:

- `llm_wiki.log`
- `run_trace.jsonl`
- `llm_wiki_stats.sqlite3`

## Manual Azure Smoke

The real-model smoke test is opt-in. Set the explicit smoke gate plus the Azure
OpenAI env vars for the desired model, then run the manual-marked test file:

```powershell
$env:KOGWISTAR_LLM_WIKI_REAL_SMOKE='1'
$env:KOGWISTAR_PARSER_PROVIDER='azure_openai'
$env:KOGWISTAR_PARSER_MODEL='gpt-5-mini'
$env:KOGWISTAR_PARSER_BASE_URL='https://<your-resource>.openai.azure.com/'
$env:OPENAI_API_KEY_GPT5_MINI='<key>'
.\.venv\Scripts\python.exe -m pytest tests\smoke\test_llm_wiki_real_azure_smoke.py -m manual -q -p no:cacheprovider
```

The test will skip if the expected Azure env values are missing or the smoke
gate is not enabled.

You can also point `KOGWISTAR_PARSER_MODEL` at `gpt-5-chat` or `gpt-5-nano`
and provide the matching `OPENAI_API_KEY_GPT5_CHAT` or
`OPENAI_API_KEY_GPT5_NANO` env var. The smoke test will resolve the matching
Azure endpoint and API version from the model-specific env suffixes when
available.

## Manual Qwen3-VL Docker Smoke

The real Docker model smoke starts the already-built CUDA Embedding Service image,
waits for `/readyz`, discovers its profile, and sends a text-plus-image request
to `/v1/represent`. It is intentionally excluded from default CI because it
requires NVIDIA Docker support, several GB of image space, and a local or
downloadable Qwen3-VL checkpoint.

```powershell
$env:KOGWISTAR_DOCKER_QWEN3_VL_E2E='1'
$env:LLM_WIKI_EMBEDDING_MODEL_REVISION='<40-character-Hugging-Face-commit-SHA>'
$env:LLM_WIKI_EMBEDDING_IMAGE='kogwistar-llm-wiki-embedding:local'
.\.venv\Scripts\python.exe -m pytest `
  tests\integration\test_qwen3_vl_docker_runtime.py `
  -m 'manual and slow' -q -p no:cacheprovider
```

To avoid downloading model weights during the test, set
`LLM_WIKI_QWEN3_VL_MODEL_DIR` to a completed local checkpoint directory. The
test bind-mounts that directory into the container read-only and uses it as
the model source. The model revision is still required for profile identity.

Build the image first with the CUDA overlay when needed:

```powershell
$env:LLM_WIKI_EMBEDDING_TORCH_BACKEND='cu128'
docker compose -f compose.yml -f compose.multimodal.yml `
  -f compose.embedding-cuda.yml build embedding
```

The test uses `--gpus all`, CUDA 12.8 Torch, dimension 1024, and the pinned
revision from the environment. It reports container logs if the model fails to
become ready. The test does not alter graph data or start PostgreSQL.

Avoid using `C:\tmp` as a pytest cache workaround unless you first verify this
process can write there:

```powershell
$p = "C:\tmp\pytest-write-probe"
New-Item -ItemType Directory -Force -Path $p
Set-Content -LiteralPath "$p\probe.txt" -Value "ok"
```

If either command fails with `Access is denied`, do not use that directory for
pytest cache.

## What The Cache Means

Pytest cache is not test semantics. Tests should not require `.pytest_cache` to
pass. It stores convenience state such as node IDs and last-failed data.

However, a bad cache path can still create noisy warnings or shutdown hangs in
local tooling. Treat cache-path problems as environment/tooling problems and
fix the cache location before debugging product code.

## Long-Run Workflow Test

The long-run ingestion workflow test is skipped unless explicitly enabled. It
defaults to a local Ollama parser, but it can also be pointed at Azure OpenAI
by setting the parser provider/model env vars:

```powershell
$env:KOGWISTAR_LLM_WIKI_LONGRUN='1'
$env:KOGWISTAR_LONGRUN_PARSER_PROVIDER='ollama'
$env:KOGWISTAR_LONGRUN_PARSER_MODEL='gemma4:e2b'
$env:KOGWISTAR_LONGRUN_PARSER_BASE_URL='http://localhost:11434'
.\.venv\Scripts\python.exe -m pytest -m "longrun" tests/integration/test_longrun_workflow_ingestion.py -q -p no:cacheprovider
```

Azure OpenAI example:

```powershell
$env:KOGWISTAR_LLM_WIKI_LONGRUN='1'
$env:KOGWISTAR_LONGRUN_PARSER_PROVIDER='azure_openai'
$env:KOGWISTAR_LONGRUN_PARSER_MODEL='gpt4o'
$env:KOGWISTAR_LONGRUN_PARSER_BASE_URL='https://<your-resource>.openai.azure.com/'
$env:KOGWISTAR_LONGRUN_PARSER_API_KEY_ENV='OPENAI_API_KEY_GPT4O'
.\.venv\Scripts\python.exe -m pytest -m "longrun" tests/integration/test_longrun_workflow_ingestion.py -q -p no:cacheprovider
```

Use `-p no:cacheprovider` for a quick local signal on Windows. The test writes a
diagnostic dump after the run starts, including raw documents, status
transitions, graph/projection summaries, maintenance evidence, promotion
evidence packs, failure records, and a final report.
### Local Linux PyPy 3.11 development container

On Windows, use Docker Desktop only as a local Linux development environment;
this is not a self-hosted GitHub runner. The helper builds the pinned Linux
PyPy 3.11 image, mounts the current checkout, creates the virtual environment
inside the container, and runs the shared provider-free test profile:

```powershell
.\scripts\run_pypy311_linux_ci.ps1
```

Use `-SkipBuild` when the local image is already current. GitHub Actions uses
the hosted `actions/setup-python@v7` PyPy matrix directly; it does not depend on
this Docker helper.
