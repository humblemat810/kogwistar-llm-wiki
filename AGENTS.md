# Agent Notes

## Testing

- Before treating a pytest timeout-after-success as an application failure,
  check the pytest cache path. On this Windows workspace, unwritable cache
  directories have repeatedly caused commands to reach `100% passed` and then
  hang at shutdown.
- Prefer the configured repo-local cache. For vendored `kogwistar`, this is
  `cache_dir = .pytest-local-cache` in `kogwistar/pytest.ini`.
- Do not use `-o cache_dir=C:\tmp\...` unless a write probe proves this process
  can create directories there.
- For a quick signal when cache behavior is suspect, run pytest with
  `-p no:cacheprovider`.
- See `doc/testing_guide.md` before inventing a new pytest cache workaround.

