# Environment Cheat Sheet

## 1. Repo Status

- Docs-first repository
- Python tooling scaffold added
- GitHub-first default install for `kogwistar`, `graph-knowledge-doc-parser`, and `kogwistar-obsidian-sink`
- Python target range: 3.13 through 3.14
- Local editable bootstrap is opt-in
- `uv` is used for the install commands below

## 2. Setup Commands

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
uv pip install -e ".[dev,test]"
```

Optional local bootstrap:

```powershell
bash scripts/bootstrap-dev.sh
```

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

- No app entrypoint yet
- No source package yet
- No automated tests yet
