# Quickstart

## 1. Create an Environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
uv pip install -e ".[dev,test]"
```

## 2. Optional Local Bootstrap

If you want the checked-out local repos to win, run:

```bash
bash scripts/bootstrap-dev.sh
```

That is the only step that switches the environment from the GitHub-first default to local editable
checkouts.

## 3. Notes

- GitHub-first is the normal path.
- Local subtrees win only after the bootstrap script is run.
- Windows users can run the Bash script from Git Bash or WSL.

