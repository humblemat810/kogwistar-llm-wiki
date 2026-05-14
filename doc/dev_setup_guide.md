# Dev Setup Guide

## 1. What This Repo Is

This repository contains the `kogwistar-llm-wiki` application package plus the
supporting docs and tests that describe its runtime contract.

At the moment it contains:

- the llm-wiki application package under `src/`
- architecture notes and workflow maps
- responsibility boundaries
- contract catalogs

The application package imports sibling runtime repos, so setup needs to keep
those sibling checkouts and package metadata aligned.

## 2. Prerequisites

- Python 3.13+
- Git
- SSH access to `github.com` if you plan to install the vendored repos directly from GitHub
- A terminal with access to the repo root
- `uv` for the optional manual install and common check commands below

## 3. Create a Virtual Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If PowerShell script execution is restricted, run the activation step from an approved shell policy
or use your preferred local environment workflow.

## 4. Install Dev Tools

First, make the sibling runtime repos available locally:

```bash
bash scripts/bootstrap-dev.sh
```

That script clones local checkouts when needed, editable-installs the sibling
runtime repos with `pip install -e`, and then installs `kogwistar-llm-wiki`
itself the same way.

If you prefer to drive installation manually, keep the same order:

```powershell
pip install -e ./kogwistar
pip install -e ./kogwistar-obsidian-sink
pip install -e ./kg-doc-parser
uv pip install -e ".[dev]"
```

The runtime dependency bundle is:

- `kogwistar`
- `kg-doc-parser`
- `kogwistar-obsidian-sink`

This is a real application package, so those sibling repos are part of the
runtime contract rather than optional tooling.

`[tool.uv.sources]` in `pyproject.toml` tells `uv` to prefer the local sibling
checkouts when they are present. It does not replace the real dependency
declarations, and `pip` ignores it.

During cross-repo refactors, those sibling repos are expected to be edited
together from the same project root. Treat them as coordinated local sources,
not as read-only vendor blobs, while still respecting the documented ownership
boundaries for reusable core versus app policy.

## 5. Common Checks

```powershell
uv run ruff check .
uv run ruff format .
uv run pytest
```

There may not be any tests yet, so `pytest` can be empty until code lands.

### Pytest cache warning

If pytest reaches `100% passed` and then hangs until timeout, check the cache
directory before debugging application logic. This has happened repeatedly on
Windows when `.pytest_cache` or `C:\tmp` was not writable by the test process.
See [Testing Guide](testing_guide.md) for the current mitigation.

## 6. Suggested Working Loop

1. Edit docs or code.
2. Run `uv run ruff check .` for style and import issues.
3. Run `uv run ruff format .` if formatting is needed.
4. Run `uv run pytest` when tests exist.
5. Commit once the repo is clean.

## 7. Notes

- Keep generated files out of version control.
- Prefer deterministic paths and repeatable setup steps.
- Update this guide if the project adopts a different build tool or package layout.
