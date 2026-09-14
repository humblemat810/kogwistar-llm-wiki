#!/usr/bin/env bash
set -euo pipefail

output_path="${LLM_WIKI_CODEX_CA_BUNDLE_FILE:-${TMPDIR:-/tmp}/llm-wiki-codex-ca/ca-bundle.pem}"
start_compose=0
project="llm-wiki-memory"

while (($#)); do
  case "$1" in
    --output) output_path="$2"; shift 2 ;;
    --project) project="$2"; shift 2 ;;
    --start-compose) start_compose=1; shift ;;
    -h|--help)
      printf '%s\n' 'Usage: setup_codex_ca.sh [--output PATH] [--project NAME] [--start-compose]'
      exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$output_path")"
system_bundle=""
for candidate in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/ca-bundle.pem; do
  if [[ -s "$candidate" ]]; then system_bundle="$candidate"; break; fi
done

if [[ -z "$system_bundle" ]] && command -v trust >/dev/null 2>&1; then
  trust extract --format=pem-bundle --filter=ca-anchors --overwrite --output="$output_path"
elif [[ -n "$system_bundle" ]]; then
  cp "$system_bundle" "$output_path"
else
  printf '%s\n' 'No usable Linux CA trust bundle was found.' >&2
  printf '%s\n' 'Install/register the proxy root with the OS trust store, then rerun this helper.' >&2
  exit 1
fi

if ! grep -q -- '-----BEGIN CERTIFICATE-----' "$output_path"; then
  printf '%s\n' "The generated bundle is empty: $output_path" >&2
  exit 1
fi

chmod 600 "$output_path"
printf 'Created %s from the Linux trusted root store.\n' "$output_path"
printf '%s\n' 'The bundle contains public certificates only; TLS verification remains enabled.'
export LLM_WIKI_CODEX_CA_BUNDLE_FILE="$output_path"

if ((start_compose)); then
  docker compose -p "$project" \
    -f compose.yml -f compose.memory-agent.yml -f compose.codex.yml \
    -f compose.codex-memory.yml -f compose.codex-ca.yml up -d
else
  printf 'Export for the current shell: export LLM_WIKI_CODEX_CA_BUNDLE_FILE=%q\n' "$output_path"
  printf '%s\n' 'Add compose.codex-ca.yml to the Compose files when starting the stack.'
fi
