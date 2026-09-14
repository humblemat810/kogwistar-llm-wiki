#!/usr/bin/env bash
set -euo pipefail

mode=standalone
project=llm-wiki-memory
build=0
login=0
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_dir"
while (($#)); do
  case "$1" in
    --mode) mode="$2"; shift 2 ;;
    --project) project="$2"; shift 2 ;;
    --build) build=1; shift ;;
    --login) login=1; shift ;;
    -h|--help) printf '%s\n' 'Usage: start_codex_compose.sh [--mode standalone|memory] [--project NAME] [--build] [--login]'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if [[ "$mode" != standalone && "$mode" != memory ]]; then
  printf '%s\n' '--mode must be standalone or memory' >&2
  exit 2
fi

bundle="${TMPDIR:-/tmp}/llm-wiki-codex-ca/ca-bundle.pem"
bash "$script_dir/setup_codex_ca.sh" --output "$bundle"
export LLM_WIKI_CODEX_CA_BUNDLE_FILE="$bundle"
files=(-f compose.codex.yml -f compose.codex-ca.yml)
if [[ "$mode" == memory ]]; then
  files=(-f compose.yml -f compose.memory-agent.yml "${files[@]}" -f compose.codex-memory.yml)
fi
if ((build)); then docker compose -p "$project" "${files[@]}" build codex; fi
if ((login)); then docker compose -p "$project" "${files[@]}" run --rm -it --entrypoint codex codex login --device-auth; fi
docker compose -p "$project" "${files[@]}" up -d
