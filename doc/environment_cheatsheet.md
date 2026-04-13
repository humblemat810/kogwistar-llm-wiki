# Environment Cheat Sheet

## 1. Repo Status

- Docs-first repository
- Python tooling scaffold added
- No runtime package yet
- Python target range: 3.13 through 3.14
- Runtime deps are installed from GitHub via `pyproject.toml`

## 2. Setup Commands

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Other useful installs:

```powershell
pip install -e ".[test]"
pip install -e ".[chroma]"
pip install -e ".[pg]"
```

## 3. Everyday Commands

```powershell
git status --short
rg --files
ruff check .
ruff format .
pytest
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
