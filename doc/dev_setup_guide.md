# Dev Setup Guide

## 1. What This Repo Is

This repository is currently a docs-first workspace for the Kogwistar LLM-Wiki effort.

At the moment it contains:

- architecture notes
- workflow maps
- responsibility boundaries
- contract catalogs

There is not yet an application package in this repo, so the setup below is a baseline for Python
tooling and future code.

## 2. Prerequisites

- Python 3.13 through 3.14
- Git
- SSH access to `github.com` if you plan to install the vendored repos directly from GitHub
- A terminal with access to the repo root

## 3. Create a Virtual Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If PowerShell script execution is restricted, run the activation step from an approved shell policy
or use your preferred local environment workflow.

## 4. Install Dev Tools

```powershell
pip install -e ".[dev]"
```

That installs the baseline developer tools declared in `pyproject.toml`.

The runtime dependencies are pulled from GitHub via direct references:

- `kogwistar`
- `graph-knowledge-doc-parser`
- `kogwistar-obsidian-sink`

Common extra combinations:

- `pip install -e ".[test]"`
- `pip install -e ".[chroma]"`
- `pip install -e ".[pg]"`

## 5. Common Checks

```powershell
ruff check .
ruff format .
pytest
```

There may not be any tests yet, so `pytest` can be empty until code lands.

## 6. Suggested Working Loop

1. Edit docs or code.
2. Run `ruff check .` for style and import issues.
3. Run `ruff format .` if formatting is needed.
4. Run `pytest` when tests exist.
5. Commit once the repo is clean.

## 7. Notes

- Keep generated files out of version control.
- Prefer deterministic paths and repeatable setup steps.
- Update this guide if the project adopts a different build tool or package layout.
