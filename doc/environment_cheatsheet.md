# Environment Cheat Sheet

## 1. Repo Status

- `kogwistar-llm-wiki` application package plus docs and tests
- Python target range: 3.13+
- Runtime bundle includes `kogwistar`, `kg-doc-parser`, and `kogwistar-obsidian-sink`
- Local editable bootstrap is the recommended repo-local setup path
- `uv` can prefer local sibling checkouts via `[tool.uv.sources]`

## 2. Setup Commands

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
bash scripts/bootstrap-dev.sh
```

If you want to install manually instead, editable-install the sibling repos
first and then install `kogwistar-llm-wiki`.

## 3. Everyday Commands

```powershell
git status --short
rg --files
uv run ruff check .
uv run ruff format .
uv run pytest
```

## 4. Useful Paths

- `pyproject.toml` - Python and tool configuration
- `.gitignore` - local files that should stay untracked
- `doc/dev_setup_guide.md` - setup steps and workflow
- `doc/environment_cheatsheet.md` - fast command reference

## 5. Current Gaps

- Keep the sibling runtime repos checked out locally when working on the app
- Keep docs and package metadata aligned with the real import graph
- Run the smoke tests after packaging or CLI changes
