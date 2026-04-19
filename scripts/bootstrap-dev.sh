#!/usr/bin/env bash
# bootstrap-dev.sh
# -----------------
# Ensures all sibling repos are present as local checkouts, then installs
# everything editable in the correct order.
#
# For each sibling repo:
#   - If the local directory already exists  → keep it (no re-clone)
#   - If it does not exist                   → clone from GitHub
#   Then install as editable from local path.
#
# This package (kogwistar-llm-wiki) is installed last, after all siblings.
#
# Usage:
#   bash scripts/bootstrap-dev.sh
#
# Windows: run from Git Bash or WSL.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- Repo catalogue ----------------------------------------------------------
# Each entry: "local-dir-name|github-url"

REPOS=(
  "kogwistar|https://github.com/humblemat810/kogwistar.git"
  "kogwistar-obsidian-sink|https://github.com/humblemat810/kogwistar-obsidian-sink.git"
  "kg-doc-parser|https://github.com/humblemat810/kg-doc-parser.git"
)

# ---- Helpers -----------------------------------------------------------------

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'ERROR: required command not found: %s\n' "$1" >&2
    exit 1
  fi
}

has_python_package() {
  local dir="$1"
  [ -f "$dir/pyproject.toml" ] || [ -f "$dir/setup.py" ] || [ -f "$dir/setup.cfg" ]
}

ensure_local() {
  local name="$1"
  local git_url="$2"
  local local_dir="$ROOT_DIR/$name"

  if [ -d "$local_dir" ]; then
    printf '[keep]  %s — already present at %s\n' "$name" "$local_dir"
  else
    printf '[clone] %s — cloning from %s\n' "$name" "$git_url"
    git clone "$git_url" "$local_dir"
  fi
}

install_editable() {
  local name="$1"
  local local_dir="$ROOT_DIR/$name"

  if has_python_package "$local_dir"; then
    printf '[install] %s — editable from %s\n' "$name" "$local_dir"
    pip install --no-deps -e "$local_dir"
  else
    printf '[skip]  %s — no Python package metadata found at %s\n' "$name" "$local_dir" >&2
  fi
}

# ---- Main --------------------------------------------------------------------

require_cmd git
require_cmd pip

printf '=== LLM-Wiki bootstrap ===\n'
printf 'Root: %s\n\n' "$ROOT_DIR"

# Step 1: Ensure all sibling repos are local (clone if missing)
printf '--- Step 1: ensure local checkouts ---\n'
for entry in "${REPOS[@]}"; do
  name="${entry%%|*}"
  git_url="${entry##*|}"
  ensure_local "$name" "$git_url"
done

# Step 2: Install sibling repos as editable from local
printf '\n--- Step 2: install siblings editable ---\n'
for entry in "${REPOS[@]}"; do
  name="${entry%%|*}"
  install_editable "$name"
done

# Step 3: Install this package last
printf '\n--- Step 3: install kogwistar-llm-wiki ---\n'
cd "$ROOT_DIR"
pip install -e ".[dev]"

printf '\n=== Bootstrap complete ===\n'
printf 'Local paths in use:\n'
for entry in "${REPOS[@]}"; do
  name="${entry%%|*}"
  printf '  %s -> %s/%s\n' "$name" "$ROOT_DIR" "$name"
done
printf '\nVerify with: pip show kogwistar kogwistar-obsidian-sink kg-doc-parser\n'
