# LLM-Wiki Cookbook

This cookbook is a task-oriented runbook for operating LLM-Wiki as a small
knowledge system. It composes the existing CLI, ingestion pipeline, workers,
workbench, agent gateway, archive service, and Docker deployment. The detailed
contracts remain in the linked guides.

All examples use `python -m kogwistar_llm_wiki`, which works from an editable
checkout and from an installed environment. Replace it with `llm-wiki` after
the package is installed.

## Choose A Path

| Goal | Path | Backend guidance |
|---|---|---|
| Try the product with fake data | `demo` | One process, in-memory, safe for local testing |
| Build a durable article collection | `ingest` plus daemons | PostgreSQL/pgvector for separate processes |
| Connect an external agent | `workbench` or `mcp` | Use authentication outside localhost |
| Recover or migrate data | `archive` | Operator-only, isolated target required |
| Compare embedding configurations | `embeddings inspect` | Do not mix profiles in one physical store |

The one-process `demo` is the recommended first run. Embedded local Chroma is
not a safe shared directory for independent writer processes. See the
[Docker deployment guide](docker_deployment.md) for the container boundary and
[Quickstart](../QUICKSTART.md) for installation.

## Recipe 1: Try A Knowledge Article

Create a small article and run the complete fake-provider path. This ingests,
maintains, projects, and exits without a real LLM or external service.

PowerShell:

```powershell
$demoRoot = ".\\logs\\llm_wiki_demo"
New-Item -ItemType Directory -Force $demoRoot, "$demoRoot\\vault" | Out-Null
@'
# Release notes

The service supports staged rollout. Roll back when error rate exceeds the
release threshold and record the decision in the change log.
'@ | Set-Content -Encoding utf8 "$demoRoot\\release-notes.md"

python -m kogwistar_llm_wiki demo `
  --workspace demo `
  --source "$demoRoot\\release-notes.md" `
  --vault "$demoRoot\\vault" `
  --title "Release notes" `
  --source-format markdown `
  --promotion-mode sync
```

Bash:

```bash
demo_root="logs/llm_wiki_demo"
mkdir -p "$demo_root/vault"
cat > "$demo_root/release-notes.md" <<'EOF'
# Release notes

The service supports staged rollout. Roll back when error rate exceeds the
release threshold and record the decision in the change log.
EOF

python -m kogwistar_llm_wiki demo \
  --workspace demo \
  --source "$demo_root/release-notes.md" \
  --vault "$demo_root/vault" \
  --title "Release notes" \
  --source-format markdown \
  --promotion-mode sync
```

Success means the command exits zero, prints a JSON summary, and writes notes
under the vault. The default parser and embedder are deterministic test-safe
implementations. Configure parser providers only when testing real model
behavior; see [provider examples in Quickstart](../QUICKSTART.md#8-real-provider-examples).

## Recipe 2: Ingest An Article Collection

The `demo` command also accepts a directory containing top-level Markdown
articles. Files named `index.md` and `manifest.md` are ignored. This remains a
single-process demo and is useful for a fast corpus check:

```bash
python -m kogwistar_llm_wiki demo \
  --workspace learning-kb \
  --source ./articles \
  --vault ./logs/learning-kb/vault \
  --source-format markdown \
  --promotion-mode sync
```

For a durable collection, ingest one article per invocation into a shared
backend. Keep the workspace ID, source URI/path, and backend settings stable.
The CLI has no hidden bulk-write path, so each article remains independently
observable and retryable.

PowerShell:

```powershell
$workspace = "engineering-kb"
$dataDir = ".\\data"
$dsn = "postgresql://user:pass@localhost:5432/llm_wiki"

Get-ChildItem .\\articles -Recurse -File -Include *.md,*.txt |
  ForEach-Object {
    python -m kogwistar_llm_wiki `
      --data-dir $dataDir --backend postgres --dsn $dsn `
      ingest --workspace $workspace --source $_.FullName `
      --title $_.BaseName --source-format markdown --promotion-mode sync
  }
```

Bash:

```bash
workspace="engineering-kb"
data_dir="./data"
dsn="postgresql://user:pass@localhost:5432/llm_wiki"

find ./articles -type f \( -name '*.md' -o -name '*.txt' \) -print0 |
while IFS= read -r -d '' article; do
  title="$(basename "$article")"
  python -m kogwistar_llm_wiki \
    --data-dir "$data_dir" --backend postgres --dsn "$dsn" \
    ingest --workspace "$workspace" --source "$article" \
    --title "$title" --source-format markdown --promotion-mode sync
done
```

Run the same ingestion command after changing an article to capture its next
source revision. Agent clients use `reingest` when replacing content through
MCP or the gateway. Keep raw source files available for provenance, archive,
and recovery.

## Recipe 3: Run Maintenance And Projection

Use the same persistent backend settings in both worker processes. Stop each
with `Ctrl-C`; the daemons finish their current bounded cycle and close their
resources.

```bash
python -m kogwistar_llm_wiki \
  --data-dir ./data --backend postgres --dsn "$dsn" \
  daemon maintenance --workspace engineering-kb --interval 10

python -m kogwistar_llm_wiki \
  --data-dir ./data --backend postgres --dsn "$dsn" \
  daemon projection --workspace engineering-kb \
  --vault ./data/vault --interval 5
```

For PowerShell, use `$dsn` instead of `"$dsn"`. Check completion with:

```bash
python -m kogwistar_llm_wiki report \
  --workspace engineering-kb --data-dir ./data \
  --backend postgres --dsn "$dsn" --dump-mode summary
```

Inspect maintenance and projection job state before treating an empty query
as a failure. A healthy workspace can legitimately have no matching evidence.

## Recipe 4: Query The Knowledge Base

For a local human workbench:

```bash
python -m kogwistar_llm_wiki \
  --data-dir ./data --backend postgres --dsn "$dsn" \
  workbench --workspace engineering-kb --host 127.0.0.1 --port 8765
```

The workbench exposes grounded lens and answer routes. A deterministic request
is provider-free:

```bash
curl -s http://127.0.0.1:8765/api/ask \
  -H 'content-type: application/json' \
  -d '{"workspace_id":"engineering-kb","session_id":"ops-1","mode":"deterministic","query_text":"What is the rollout policy?"}'
```

Inspect the cited entities and source watermark. A response with
`insufficiency` or `no_change` is valid when the evidence is not enough.

## Recipe 5: Serve Agents

Start the MCP service when the client needs the eleven semantic tools. For
REST/OpenAI-shaped requests or A2A, start `workbench` with agent routes enabled.

```bash
export LLM_WIKI_AGENT_API_ENABLED=true
export LLM_WIKI_MCP_AUTH_REQUIRED=true
export LLM_WIKI_MCP_TOKEN='use-a-secret-from-your-secret-store'

python -m kogwistar_llm_wiki \
  --data-dir ./data --backend postgres --dsn "$dsn" \
  mcp --workspace engineering-kb --transport streamable-http \
  --host 127.0.0.1 --port 8780 --path /mcp
```

Use `query` and `search` for reads; `ingest`, `source`, and `reingest` for
source lifecycle; `maintain` for directed asynchronous work; `status` for
health; `hypergraph_search` and `history` for advanced reads; and
`propose` followed by explicit `confirm` for mutations. Internal queues,
database operations, and arbitrary graph writes stay hidden.

See the [agent gateway guide](agent_gateway_quickstart.md) and the
[Claude, Hermes, Pi, and Codex integration examples](../integrations/) for
client-specific configuration. Do not bind an unauthenticated service beyond
localhost.

## Recipe 6: Personal Mode And Shared Identity

For a trusted local single user, explicitly disable identity and ACL checks:

```powershell
$env:LLM_WIKI_AUTH_MODE = "disabled"
$env:LLM_WIKI_AGENT_API_ENABLED = "true"
python -m kogwistar_llm_wiki workbench --workspace personal --port 8765
```

This is a special no-identity mode, not a default user. The workspace remains
the data boundary. Before sharing the service, switch to static-token or JWT
mode and provision workspace membership. Retaining the workspace ID preserves
the graph during this configuration migration, but personal records do not
receive retroactive principal ownership.

## Recipe 7: Embedding Compatibility

Inspect a persistent store before changing provider, model, dimension, endpoint,
or metric:

```bash
python -m kogwistar_llm_wiki \
  --data-dir ./data --backend postgres --dsn "$dsn" \
  embeddings inspect --workspace engineering-kb
```

The check is fail-closed. PostgreSQL shared vector tables require one exact
profile. Chroma profiles are bound to their persistent graph stores. Do not
alter a populated vector column in place or assume equal dimensions imply
equal semantics. Prefer archive, isolated restore, re-embedding, validation,
and cutover. Legacy Chroma adoption is operator-only and requires an explicit
acknowledgement after verifying the old profile.

## Recipe 8: Archive And Recovery

Archives are operator-only and require quiescent writers. Create and verify a
portable event archive before any destructive recovery operation:

```bash
python -m kogwistar_llm_wiki \
  --data-dir ./data --backend postgres --dsn "$dsn" \
  archive create --workspace engineering-kb \
  --output ./archives/engineering-kb-base.tar.gz

python -m kogwistar_llm_wiki archive verify \
  --archive ./archives/engineering-kb-base.tar.gz
```

Restore defaults to dry-run and writes nothing. Review the report, then use a
fresh isolated target and the explicit `--apply` flag:

```bash
python -m kogwistar_llm_wiki \
  --data-dir ./restore-data --backend postgres --dsn "$restore_dsn" \
  archive restore --archive ./archives/engineering-kb-base.tar.gz \
  --target-workspace engineering-kb-recovered

python -m kogwistar_llm_wiki \
  --data-dir ./restore-data --backend postgres --dsn "$restore_dsn" \
  archive restore --archive ./archives/engineering-kb-base.tar.gz \
  --target-workspace engineering-kb-recovered --apply
```

Fast backend snapshots are optional accelerators, not event truth. Use them
only when backend and embedding fingerprints match; portable replay remains the
recovery path for remapped workspaces or incompatible embeddings. Never put
credentials or bearer tokens into an archive.

## Recipe 9: Docker Operations

The Compose stack provides separate REST and MCP services over shared
PostgreSQL/pgvector storage:

```bash
export POSTGRES_PASSWORD='change-this-development-password'
docker compose up --build
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/readyz
```

`healthz` reports process health; `readyz` reports whether dependencies and
owned engines can accept work. Changing environment variables requires a
container recreation:

```bash
docker compose up -d --force-recreate
```

`docker compose down` preserves named volumes. `docker compose down -v` is a
destructive development reset; export or inspect the graph first.

## Recipe 10: Codex App Server Cockpit

This path requires a host installation and an authenticated Codex subscription
or configured provider. The container does not contain Codex. Start the
workbench with the App Server transport:

```bash
KOGWISTAR_CODEX_TRANSPORT=app_server \
python -m kogwistar_llm_wiki workbench \
  --workspace engineering-kb --codex-transport app_server
```

The adapter starts one bounded local App Server child per turn, requests typed
output, and keeps graph mutation behind proposal validation and confirmation.
Use the default deterministic mode or fake App Server tests in CI; live Codex
subscription checks are manual.

## Troubleshooting Checklist

- Confirm every process uses the same workspace and persistent backend settings.
- Check `/healthz` and `/readyz` separately before investigating empty results.
- Run `report` and inspect pending, doing, failed, and completed maintenance jobs.
- Inspect embedding profiles before changing a model or dimension.
- Verify an archive before attempting restore; use dry-run before `--apply`.
- Treat an article as non-authoritative until its source, provenance, readiness,
  and promotion state are visible.
- For a hung pytest run after `100% passed`, inspect the configured cache path
  and use the repository guidance in [testing guide](testing_guide.md).

