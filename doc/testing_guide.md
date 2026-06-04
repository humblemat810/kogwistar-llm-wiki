# Testing Guide

This guide captures local test-running pitfalls that have already cost time.
Please update it when a failure mode repeats.

## Pytest Cache On Windows

If a pytest command reaches `100% passed` and then hangs until the command
timeout, check the pytest cache directory before investigating application
logic.

Treat that pattern as a cache-shutdown problem first, not as a product
regression. On this Windows workspace, the test process has repeatedly reached
`100% passed` and only hung while pytest was shutting down its cache provider.

Observed local failure mode:

- `kogwistar/.pytest_cache` existed but was not writable by the test process.
- `-o cache_dir=C:\tmp\...` also hung because this process could not create
  directories under `C:\tmp`.
- Disabling the cache with `-p no:cacheprovider` made tests exit cleanly, but
  the better default is to use a writable repo-local cache directory.

Current mitigation:

- Vendored `kogwistar/pytest.ini` sets `cache_dir = .pytest-local-cache`.
- `.pytest-local-cache` is ignored by the vendored repo's `.gitignore`.

Recommended commands:

```powershell
# Normal run; uses the repo-local cache configured by pytest.ini.
.\.venv\Scripts\python.exe -m pytest kogwistar/tests/core/test_job_queue_subsystem.py -q

# If cache permissions are suspect and you only need a signal, disable cache.
.\.venv\Scripts\python.exe -m pytest <test-target> -q -p no:cacheprovider
```

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
