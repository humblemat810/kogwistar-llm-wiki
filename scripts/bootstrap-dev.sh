#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

KOGWISTAR_URL="https://github.com/humblemat810/kogwistar.git"
KG_DOC_PARSER_URL="https://github.com/humblemat810/kg-doc-parser.git"
KOGWISTAR_OBSIDIAN_SINK_URL="https://github.com/humblemat810/kogwistar-obsidian-sink.git"

KOGWISTAR_DIR="$ROOT_DIR/kogwistar"
KG_DOC_PARSER_DIR="$ROOT_DIR/kg-doc-parser"
KOGWISTAR_OBSIDIAN_SINK_DIR="$ROOT_DIR/kogwistar-obsidian-sink"
require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

ensure_checkout() {
  local name="$1"
  local url="$2"
  local dir="$3"

  if [ -d "$dir" ]; then
    printf '[keep] %s exists at %s\n' "$name" "$dir"
    return 0
  fi

  printf '[clone] %s -> %s\n' "$name" "$dir"
  git clone "$url" "$dir"
}

install_editable_if_present() {
  local name="$1"
  local dir="$2"

  if [ -f "$dir/pyproject.toml" ] || [ -f "$dir/setup.py" ]; then
    printf '[install] %s editable from %s\n' "$name" "$dir"
    uv pip install --no-deps -e "$dir"
  else
    printf '[skip] %s has no Python packaging metadata at %s\n' "$name" "$dir" >&2
  fi
}

require_cmd git
require_cmd uv

ensure_checkout "kogwistar" "$KOGWISTAR_URL" "$KOGWISTAR_DIR"
ensure_checkout "kg-doc-parser" "$KG_DOC_PARSER_URL" "$KG_DOC_PARSER_DIR"
ensure_checkout "kogwistar-obsidian-sink" "$KOGWISTAR_OBSIDIAN_SINK_URL" "$KOGWISTAR_OBSIDIAN_SINK_DIR"

printf '[install] base repo from pyproject.toml\n'
(
  cd "$ROOT_DIR"
  uv pip install -e ".[dev,test]"
)

install_editable_if_present "kg-doc-parser" "$KG_DOC_PARSER_DIR"
install_editable_if_present "kogwistar-obsidian-sink" "$KOGWISTAR_OBSIDIAN_SINK_DIR"
install_editable_if_present "kogwistar" "$KOGWISTAR_DIR"

printf 'Expected active kogwistar import path: %s\n' "$KOGWISTAR_DIR/kogwistar/__init__.py"
printf 'Bootstrap complete.\n'
