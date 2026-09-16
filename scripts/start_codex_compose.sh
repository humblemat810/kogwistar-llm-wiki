#!/usr/bin/env bash
set -euo pipefail

mode=standalone
project=llm-wiki-memory
build=0
login=0
stack=split
embedding=none
env_file=.env
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"
cd "$repo_dir"
while (($#)); do
  case "$1" in
    --mode) mode="$2"; shift 2 ;;
    --project) project="$2"; shift 2 ;;
    --build) build=1; shift ;;
    --login) login=1; shift ;;
    --stack) stack="$2"; shift 2 ;;
    --embedding) embedding="$2"; shift 2 ;;
    --env-file) env_file="$2"; shift 2 ;;
    -h|--help) printf '%s\n' 'Usage: start_codex_compose.sh [--mode standalone|memory] [--project NAME] [--stack split|combined] [--embedding none|vllm|transformers] [--env-file PATH] [--build] [--login]'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if [[ "$mode" != standalone && "$mode" != memory ]]; then
  printf '%s\n' '--mode must be standalone or memory' >&2
  exit 2
fi
if [[ "$stack" != split && "$stack" != combined ]]; then
  printf '%s\n' '--stack must be split or combined' >&2
  exit 2
fi
if [[ "$embedding" != none && "$embedding" != vllm && "$embedding" != transformers ]]; then
  printf '%s\n' '--embedding must be none, vllm, or transformers' >&2
  exit 2
fi

bundle="${TMPDIR:-/tmp}/llm-wiki-codex-ca/ca-bundle.pem"
bash "$script_dir/setup_codex_ca.sh" --output "$bundle"
export LLM_WIKI_CODEX_CA_BUNDLE_FILE="$bundle"
files=(-f compose.codex.yml -f compose.codex-ca.yml)
if [[ "$mode" == memory ]]; then
  files=(-f compose.yml -f compose.memory-agent.yml "${files[@]}" -f compose.codex-memory.yml)
  if [[ "$stack" == combined ]]; then files+=( -f compose.combined.yml ); fi
  if [[ "$embedding" == vllm ]]; then files+=( -f compose.embedding-vllm.yml ); fi
  if [[ "$embedding" == transformers ]]; then files+=( -f compose.multimodal.yml ); fi
fi
compose=(docker compose)
if [[ -f "$env_file" ]]; then compose+=(--env-file "$env_file"); fi
if ((build)); then "${compose[@]}" -p "$project" "${files[@]}" build codex; fi
if ((login)); then "${compose[@]}" -p "$project" "${files[@]}" run --rm -it --entrypoint codex codex login --device-auth; fi
"${compose[@]}" -p "$project" "${files[@]}" up -d
