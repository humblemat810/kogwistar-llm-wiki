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
# Default PR CI slice; includes tests auto-marked as `ci`.
.\.venv\Scripts\python.exe -m pytest tests -q -m ci

# Normal run; uses the repo-local cache configured by pytest.ini.
.\.venv\Scripts\python.exe -m pytest kogwistar/tests/core/test_job_queue_subsystem.py -q

# If cache permissions are suspect and you only need a signal, disable cache.
.\.venv\Scripts\python.exe -m pytest <test-target> -q -p no:cacheprovider
```

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
