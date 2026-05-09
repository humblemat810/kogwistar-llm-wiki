# Testing Guide

This guide captures local test-running pitfalls that have already cost time.
Please update it when a failure mode repeats.

## Pytest Cache On Windows

If a pytest command reaches `100% passed` and then hangs until the command
timeout, check the pytest cache directory before investigating application
logic.

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

