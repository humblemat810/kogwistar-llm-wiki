# CLI Reference - `llm-wiki`

All commands are available after installing the package and its sibling runtime
dependencies:

```bash
bash scripts/bootstrap-dev.sh
```

## Top-Level

```text
llm-wiki [--data-dir <path>] [--split-derived-knowledge] <command>
```

| Option | Default | Description |
|---|---|---|
| `--data-dir` | none | Path to persistent data directory; falls back to `KOGWISTAR_DATA_DIR` when omitted |
| `--split-derived-knowledge` | off | Host `derived_knowledge` on a separate engine |

Hosting tradeoff for `derived_knowledge`:

- Default same-engine mode keeps curated knowledge and `derived_knowledge` on the same backend, but in different namespaces (`ws:{id}:g:curated_kg` versus `ws:{id}:derived_knowledge`).
- Split-engine mode isolates storage and indexing cost, but cross-surface search must query two engines deliberately.
- The semantic contract is the same in both layouts: `derived_knowledge` never lives in the raw curated_kg namespace.

## `llm-wiki daemon`

Both daemons poll on a configurable interval, treat `Ctrl-C` / `SIGTERM` as
graceful stop requests, and call the core `engine.recovery.recover_startup(...)`
coordinator before polling after restart.

Core startup recovery:

1. Safely repairs missing lane-message projection rows from graph/entity-event truth.
2. Reports durable queues, lane rows, checkpoints, run history, dead letters, and daemon health.
3. Leaves durable job claims and lane claims to existing lease expiry semantics.
4. Keeps workflow checkpoint auto-resume disabled unless an explicit restartable policy and resume hook are supplied.

### `daemon projection`

Drain the Obsidian projection queue for a workspace and keep the vault in sync.

```bash
llm-wiki daemon projection \
  --workspace <workspace-id> \
  --vault     <path-to-obsidian-vault> \
  [--interval <seconds>]
```

Startup recovery passes these app-specific surfaces into core:

- projection manifest state
- vault materialization state
- projection daemon health

Each poll cycle:

1. Claim durable projection jobs from the queue facade.
2. Call `ProjectionManager.sync_obsidian_vault()` through the Obsidian sink.
3. Emit append-only projection status events.
4. Mark jobs done or retry/fail through `engine.jobs`.
5. Sleep `--interval` seconds before the next poll.

### `daemon maintenance`

Drain the maintenance job queue and run the synthesis plus execution-wisdom path.

```bash
llm-wiki daemon maintenance \
  --workspace <workspace-id> \
  [--interval <seconds>]
```

Startup recovery passes maintenance daemon health into core and uses the core
report for queue/lane/checkpoint/run/dead-letter visibility.

Each poll cycle:

1. Claim durable maintenance jobs from the queue facade.
2. Process `distill` and `execution_wisdom` requests.
3. Emit a reply lane message.
4. Mark the durable job `DONE` or retry/fail it through `engine.jobs`.
5. Sleep `--interval` seconds before the next poll.

Current semantics:

- `distill` writes replacement `derived_knowledge` nodes in `ws:{id}:derived_knowledge`.
- `execution_wisdom` scans failure traces and emits `execution_wisdom` nodes.
- Interrupted work is recovered by core projection repair plus lease redelivery.
- Delivery remains at-least-once; duplicate execution should converge through deterministic IDs, completion checks, and versioned replacement.

## `llm-wiki workbench`

Serve the interactive graph workbench and its durable background Codex brain:

```bash
python -m kogwistar_llm_wiki \
  --data-dir <persistent-data-directory> \
  workbench \
  --workspace <workspace-id> \
  [--host 127.0.0.1] \
  [--port 8765] \
  [--codex-workers 1] \
  [--codex-executable <path>] \
  [--codex-model <model>] \
  [--codex-profile <profile>] \
  [--codex-transport exec|app_server] \
  [--codex-timeout 300]
```

The command recovers pending workbench interactions, serves the lens/history/
proposal and interaction APIs, and invokes Codex in an ephemeral read-only
sandbox. Model activity renews the job lease; ownership is checked again before
the first terminal result is appended. Browser clients submit Codex turns to
`POST /api/interactions` and poll `GET /api/interactions` rather than holding a
model-length HTTP request open.

`exec` is the default Codex transport. Set `--codex-transport app_server` (or
`KOGWISTAR_CODEX_TRANSPORT=app_server`) to use the installed
`codex app-server --stdio` JSON-RPC protocol. The adapter starts one
ephemeral, read-only App Server thread per bounded turn and closes the child on
completion or timeout.

The current Codex worker answers from the bounded lens or returns `no_change`.
It does not receive direct graph-write access. Any future mutation proposal
still requires host validation and confirmation.

## Programmatic API Cheatsheet

```python
from pathlib import Path

from kogwistar_llm_wiki.daemon import MaintenanceDaemon, ProjectionDaemon
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline, build_persistent_namespace_engines

engines = build_persistent_namespace_engines(Path("logs/llm_wiki_data"))
pipeline = IngestPipeline(engines)

m = MaintenanceDaemon(engines, "demo", poll_interval=10.0)
p = ProjectionDaemon(engines, "demo", vault_root="/tmp/vault", poll_interval=5.0)

report = m.recover_startup_state()
print(report.repaired_count, len(report.dead_letters))
```

## Running `python -m kogwistar_llm_wiki`

Equivalent to the `llm-wiki` script:

```bash
python -m kogwistar_llm_wiki daemon projection --workspace demo --vault /tmp/vault
python -m kogwistar_llm_wiki --help
```

## Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `KOGWISTAR_DATA_DIR` | CLI | Fallback persistent data directory for `ingest`, `workbench`, `daemon projection`, and `daemon maintenance` when `--data-dir` is omitted |
| `KOGWISTAR_CODEX_EXECUTABLE` | workbench | Optional Codex CLI path when `codex` is not on `PATH` |
| `KOGWISTAR_CODEX_TRANSPORT` | workbench, seed-bundle | `exec` (default) or `app_server` |
| `KOGWISTAR_PARSER_PROVIDER` | CLI, parser workflows | Explicit parser provider alias. `azure_openai` is normalized to the `azure` chat provider. |
| `KOGWISTAR_PARSER_MODEL` | CLI, parser workflows | Explicit parser model or Azure deployment name |
| `KOGWISTAR_PARSER_BASE_URL` | CLI, parser workflows | Parser endpoint URL, for example Ollama base URL or Azure OpenAI endpoint |
| `KOGWISTAR_PARSER_API_KEY_ENV` | CLI, parser workflows | Env var name that holds the parser API key |
| `KOGWISTAR_PARSER_API_VERSION` | CLI, parser workflows | Azure OpenAI API version to use when building `AzureChatOpenAI` |
| `KOGWISTAR_MAINTENANCE_PROVIDER` | daemon maintenance | Explicit maintenance provider alias |
| `KOGWISTAR_MAINTENANCE_MODEL` | daemon maintenance | Explicit maintenance model or Azure deployment name |
| `KOGWISTAR_MAINTENANCE_BASE_URL` | daemon maintenance | Maintenance endpoint URL |
| `KOGWISTAR_MAINTENANCE_API_KEY_ENV` | daemon maintenance | Env var name that holds the maintenance API key |
| `PYTHONPATH` | dev | Ensure `src/` is importable without install |

`demo` and `ingest` also accept `--parser-lane page_index|workflow_layered`. Use
`workflow_layered` when you want the iterative layerwise parser path instead of
the page-index parser.

## `llm-wiki embeddings`

Inspect or explicitly adopt the embedding profile for persistent graph stores.
The profile includes provider, model, dimension, similarity metric, and a
sanitized endpoint fingerprint; credentials and API-key environment variable
names are never persisted. The profile is checked before graph writes. A
PostgreSQL physical table bundle must use one exact profile, while each
persistent Chroma graph directory has its own profile binding.

```powershell
# Read configured, registered, and physical state without binding a profile:
python -m kogwistar_llm_wiki `
  --data-dir .\data `
  --backend chroma `
  embeddings inspect `
  --workspace demo

# Explicitly attest to the configured profile for a known legacy Chroma store:
python -m kogwistar_llm_wiki `
  --data-dir .\data `
  --backend chroma `
  embeddings adopt-legacy-profile `
  --workspace demo `
  --acknowledge-legacy-vectors
```

Adoption is operator-only and does not convert or re-embed existing vectors.
For an unknown or incompatible store, prefer a portable archive, isolated
restore, re-embedding, validation, and cutover. There is no automatic in-place
dimension migration. In-memory stores are process-local and do not provide
durable profile compatibility across restarts.

## Test Commands

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ -q
.venv\Scripts\python.exe -m pytest tests/unit/test_temporary_namespace.py -q
.venv\Scripts\python.exe -m pytest tests/unit/test_projection_consistency.py -q
```

## `llm-wiki seed-bundle`

Seed a versioned, source-grounded learning graph into the curated workspace,
optionally run a real Codex cockpit review over the persisted graph, and export
the graph back to canonical JSON:

```powershell
python -m kogwistar_llm_wiki `
  --data-dir logs/workbench_seed_rl/state `
  seed-bundle `
  --workspace rl-agent-learning `
  --bundle data/seed_bundles/rl_llm_agent_tool_use_v1.json `
  --output logs/workbench_seed_rl/exported.json `
  --cockpit-question "Compare DeepSeek-R1, Kimi k1.5, and Kimi K2 from the grounded graph."
```

The command fails if source excerpts are absent, ambiguous, or inconsistent
with their half-open offsets; if IDs collide with another seed bundle; or if
the persisted export differs from the canonical input. Re-running the same
bundle in the same workspace is idempotent. Source records are persisted as
curated graph entities, so a future export does not depend on retaining the
original input file.

`--cockpit-question` is optional because it invokes the installed Codex CLI.
When supplied, the turn uses the normal read-only cockpit contract: reads are
bounded, any graph patch remains a proposal, and `no_change` is a valid result.
The interaction and investigation history are persisted before the graph is
exported and checked again. The concise cockpit outcome is printed to stdout;
the complete lens, trace, observations, and answer are written beside the
export as `<export-stem>.cockpit.json`.

The repository includes
`data/seed_bundles/rl_llm_agent_tool_use_v1.json`, covering RLHF, WebGPT,
ReAct, Toolformer, GRPO, DeepSeek-R1, Kimi k1.5, Kimi K2, and host-executed
tool calling with ordinary edges and first-class multi-endpoint hyperedges.

## `llm-wiki archive`

Create and validate operator-only portable event archives. Archives include
lossless Kogwistar event envelopes and per-namespace sequence watermarks. They
are suitable for Chroma, SQLite, and PostgreSQL-backed workspaces. The archive
timestamp is informational; restore correctness is defined by the recorded
watermarks.

Capture must be performed with LLM-Wiki writers stopped or drained. Known
pending or doing durable index jobs cause capture to fail. Do not copy a live
Chroma persistence directory.

```powershell
python -m kogwistar_llm_wiki `
  --data-dir .\data `
  --backend chroma `
  archive create `
  --workspace demo `
  --output .\archives\demo-base.tar.gz `
  --include-backend-snapshot

python -m kogwistar_llm_wiki archive verify `
  --archive .\archives\demo-base.tar.gz

# Safe dry-run (the default):
python -m kogwistar_llm_wiki `
  --data-dir .\restore-data `
  --backend chroma `
  archive restore `
  --archive .\archives\demo-base.tar.gz

# Apply an exact restore to a fresh isolated datastore. The source workspace ID
# is retained when --target-workspace is omitted:
python -m kogwistar_llm_wiki `
  --data-dir .\restore-data `
  --backend chroma `
  archive restore `
  --archive .\archives\demo-base.tar.gz `
  --apply

# Or remap the workspace ID while rebuilding derived vectors/indexes:
python -m kogwistar_llm_wiki `
  --data-dir .\restore-copy-data `
  --backend chroma `
  archive restore `
  --archive .\archives\demo-base.tar.gz `
  --target-workspace demo-copy `
  --apply

# Optional fast exact snapshot restore (requires the manifest fingerprint):
python -m kogwistar_llm_wiki `
  --data-dir .\snapshot-data `
  --backend chroma `
  archive restore `
  --archive .\archives\demo-base.tar.gz `
  --use-backend-snapshot `
  --embedding-fingerprint <manifest-embedding-fingerprint>
```

An incremental archive is created with `--parent` and restored by supplying
the parent archive path with `--parent`. `archive catalog --directory` lists
verified archives and can filter by `--before-ms`; this selects a completed
watermark archive rather than slicing events at an arbitrary timestamp.

`seed-bundle` and report dumps remain teaching/diagnostic exports. They are not
substitutes for `archive create`. Backend snapshots are only exact accelerators;
portable event restore is the migration and recovery fallback.

## `llm-wiki compose`

Generate a safe self-contained local stack without putting credentials in the
file. GPU is the practical default; pass an immutable Hugging Face revision:

```powershell
python -m kogwistar_llm_wiki compose generate `
  --output .\compose.generated.yml `
  --workspace demo `
  --model-revision <immutable-commit> `
  --with-otel
python -m kogwistar_llm_wiki compose check --file .\compose.generated.yml
```

Use `--mode cpu` for a CPU sidecar or `--mode text-only` when no multimodal
service is wanted. Use `--with-oauth` to include the optional Compose-profiled
OAuth example; it does not configure application JWT verification automatically.
Start that profile explicitly with `docker compose --profile oauth up` after
configuring issuer and verification settings. Put passwords and tokens in the
shell or `.env`, never in the generated YAML. Quote `.env` values containing
`$` with single quotes so Compose does not interpolate them. The checker runs
structural checks and, when Docker is available, `docker compose config`; it
falls back to structural checks when Docker is unavailable. After it passes,
run the service health/readiness checks.
### Compose combinations

The checked-in `compose.memory-agent.yml` is reproducible as an ordinary
configuration combination (PostgreSQL, GPU, OTel, OAuth service, and static
token authentication):

```powershell
.venv\Scripts\python.exe -m kogwistar_llm_wiki compose generate `
  --output compose.memory-agent.yml `
  --backend postgres `
  --mode gpu `
  --with-otel `
  --with-oauth `
  --auth-mode static_token `
  --model-revision <immutable-Qwen3-VL-revision>
```

The standard bundle is the same command without `--with-otel`, `--with-oauth`,
and `--auth-mode static_token`. There is deliberately no separate profile
argument; every generated deployment is the result of explicit options.

Use `--mode cpu` for a CPU sidecar or `--mode text-only` when no multimodal
service is wanted. The generated Compose target is independent of the live
settings page. The Compose generator rejects embedded Chroma because REST and
MCP are separate processes; use PostgreSQL for this bundle or the one-process
`demo` command for local Chroma. Validate it with:

```powershell
.venv\Scripts\python.exe -m kogwistar_llm_wiki compose check --file compose.memory-agent.yml
```
