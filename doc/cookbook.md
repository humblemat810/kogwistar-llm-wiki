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

## Recipe: Operating Settings

Open the graph workbench and choose **Settings** to inspect the effective
runtime configuration. The panel shows the local text embedding plane, the
optional Docker Qwen3-VL Embedding Service, parser and maintenance model,
readiness, and profile locks. Effective values are what the current process is
using; desired values are staged for a future launch and never replace them
silently.

The multimodal route can be enabled or disabled after confirmation when the
Embedding Service is already configured. Changing an embedding model,
provider, metric, or dimension requires a restart and isolated re-embedding.
The UI does not perform destructive migration or edit arbitrary environment
variables. Operators can inspect the same profile data from the CLI:

```bash
python -m kogwistar_llm_wiki embeddings inspect --workspace demo
```

## Recipe: Multimodal Embedding Service

Use the isolated FastAPI service for production Qwen3-VL inference. GPU is the
practical default for this vision model. The app
captures source units first and sends resolved, bounded asset bytes to the
sidecar only during Stage 2 promotion:

```bash
LLM_WIKI_EMBEDDING_TORCH_BACKEND=cu128 \
docker compose -f compose.yml -f compose.multimodal.yml \
  -f compose.embedding-cuda.yml up --build
```

CPU fallback for smoke tests or hosts without NVIDIA Container Toolkit:

```bash
LLM_WIKI_EMBEDDING_TORCH_BACKEND=cpu \
docker compose -f compose.yml -f compose.multimodal.yml up --build
```

Set `LLM_WIKI_EMBEDDING_MODEL_REVISION` to an immutable Hugging Face
revision before starting Compose. The sidecar is the standalone
standalone Embedding Service distribution plus its
dependency-light contract; it does not install the
LLM-Wiki application or graph stack. `/healthz` is liveness and `/readyz` is
model readiness. A live but not-ready sidecar must not receive Stage 2 work.

Use `-f compose.embedding-cuda.yml` with the CPU overlay on a host with
NVIDIA Container Toolkit. Set `LLM_WIKI_EMBEDDING_DIMENSION=1024` (the
recommended profile), `1536` for a larger pgvector profile, or `2048` only for
Chroma/non-HNSW storage. Changing model, revision, dimension, metric, or
preprocessing requires an isolated re-embedding projection.

The service does not accept source paths or URLs. LLM-Wiki resolves assets,
checks their SHA-256, and sends bounded bytes. If the service is unavailable,
Stage 1 remains durable and retryable; text/graph retrieval can continue with
an explicit partial status.

### Developer-only local adapter

The existing local installation steps below are retained for tests and
benchmarking only. They do not change the production sidecar architecture.

Native multimodal retrieval is an opt-in projection beside the canonical
knowledge graph. The native default is Qwen3-VL-Embedding-2B. Its Torch runtime is a versioned installation profile, not an
implicit local prerequisite: choose `cpu`, `cu126`, or `cu128` deliberately.
The installer uses the active virtual environment, installs the checked-in
profile before the application extra, and verifies the resulting runtime.
It never guesses from the installed NVIDIA driver.

For a local checkout, install the sibling repositories first. The bootstrap
uses one explicit Python interpreter for every pip operation, which avoids
mixing an Anaconda `pip` with another `python` on Windows:

```bash
PYTHON_BIN=.venv/bin/python bash scripts/bootstrap-dev.sh
# CPU profile:
.venv/bin/python -m pip install -r requirements/multimodal/torch-cpu.txt
.venv/bin/python -m pip install -e ".[multimodal-cpu]"
# OR NVIDIA CUDA 12.8 profile:
.venv/bin/python -m pip install -r requirements/multimodal/torch-cu128.txt
.venv/bin/python -m pip install -e ".[multimodal-cuda]"
.venv/bin/python scripts/pull_qwen3_vl_model.py \
  --local-dir data/models/qwen3-vl-embedding-2b \
  --max-workers 1
.venv/bin/python scripts/pull_qwen3_vl_model.py \
  --local-dir data/models/qwen3-vl-embedding-2b \
  --verify-only
```

PowerShell:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e .\kg-doc-parser --no-deps
& .\.venv\Scripts\python.exe -m pip install -e .\kogwistar-obsidian-sink --no-deps
# CPU profile:
& .\.venv\Scripts\python.exe -m pip install -r requirements\multimodal\torch-cpu.txt
& .\.venv\Scripts\python.exe -m pip install -e ".[multimodal-cpu]"
# OR NVIDIA CUDA 12.8 profile:
& .\.venv\Scripts\python.exe -m pip install -r requirements\multimodal\torch-cu128.txt
& .\.venv\Scripts\python.exe -m pip install -e ".[multimodal-cuda]"
& .\.venv\Scripts\python.exe scripts/pull_qwen3_vl_model.py --local-dir data/models/qwen3-vl-embedding-2b --max-workers 1
& .\.venv\Scripts\python.exe scripts/pull_qwen3_vl_model.py --local-dir data/models/qwen3-vl-embedding-2b --verify-only
```

Use one profile, not both examples. The CPU Torch wheel is installed from
the checked-in CPU requirements profile, followed by the dependency-only
`.[multimodal-cpu]` extra. CUDA dependencies are exposed as
`.[multimodal-cuda]` and must follow an explicit official Torch requirements
profile. `cpu` is the safe cross-platform default.
`cu126` and `cu128` use the corresponding official PyTorch wheel index and
fail after installation if the selected build has no usable GPU. Choose the
CUDA profile supported by the host driver; the package intentionally does not
infer compatibility.

Do not set `LLM_WIKI_MULTIMODAL_TORCH_BACKEND` for the production application
container. It is only for direct local adapter development. The production
container requires the Embedding Service URL and explicit host allowlist;
the base default remains `none` and does not import Torch. A vision endpoint
served by Ollama, vLLM, or llama.cpp is not automatically a native embedding
provider. See the ADR's remote-runtime admission rules before configuring a
future remote projection adapter.

### Embedding Service Docker

The default application image does not install Torch or request a GPU. Start
the separate CPU Embedding Service with:

```bash
docker compose -f compose.yml -f compose.multimodal.yml up --build
```

For NVIDIA CUDA 12.8, add `-f compose.embedding-cuda.yml`. The model
checkpoint is cached in the sidecar volume, not installed in REST/MCP.

The service defaults to `Qwen3-VL-Embedding-2B`, `dimension=1024`, and
`batch_size=1`. Supported dimensions are 64..2048; 1536 is the larger pgvector
option, while 2048 is intended for Chroma or non-HNSW storage.
Capture source units into Stage 1, promote them in
minibatches to Stage 2, and query only after promotion. Text-bearing units use
the text processor; image, video-frame, and visual PDF/table/chart units use
the image processor, with input order preserved. Text, images, webpage image
occurrences, and normalized PDF page/table/chart manifests can share a
Qwen3-VL dense projection; the existing knowledge/conversation embedding
spaces remain separate. ColQwen remains an explicit legacy late-interaction
comparison route and is not interchangeable. The source adapter stores references and
locators, not binary assets, so an authorized `AssetResolver` must be supplied
for externally stored images or page renders. See the [native multimodal ADR](adr_native_multimodal_embedding_support.md)
for source-map, grounding, profile, and production-scaling boundaries.

### Benchmark Multimodal Encoding

Run the provider-free benchmark in CI or during local development:

```bash
.venv/bin/python scripts/benchmark_multimodal.py --backend fake --items 4 --batch-size 4 --repeats 5
```

It reports median and mean latency plus items per second for one image, an
image batch, one text passage, a text batch, and a mixed image/text batch.
Fake timings only compare code-path and batching overhead. After installing
the native dependencies and downloading the checkpoint, measure the actual
machine or production sidecar with:

For providers that expose a tokenizer, the application uses the declared
token budget and tokenizer-aware prefix cropping before retrying. Providers
without that capability retain the older character-length defensive fallback;
it is deliberately not presented as an exact token count.

```bash
.venv/bin/python scripts/benchmark_multimodal.py \
  --backend remote \
  --service-url http://embedding:8790 \
  --allowed-host embedding \
  --items 4 \
  --batch-size 1 \
  --service-batch-size 1 \
  --repeats 3
```

When the service intentionally uses a local checkpoint directory rather than
the canonical Hugging Face model ID, provide that exact profile identity. This
keeps the model/profile compatibility check enabled during the benchmark:

```powershell
.venv\Scripts\python.exe scripts\benchmark_multimodal.py `
  --backend remote `
  --service-url http://127.0.0.1:8790 `
  --allowed-host 127.0.0.1 `
  --service-model (Resolve-Path data\models\qwen3-vl-embedding-2b).Path `
  --items 4 --batch-size 1 --service-batch-size 1 --repeats 3
```

For local encoders, `--batch-size` controls model batching. For the remote
backend, it only describes the client workload; the actual model microbatch is
set when the service starts with
`LLM_WIKI_EMBEDDING_BATCH_SIZE`. The remote benchmark requires
`--service-batch-size` and verifies it against `/v1/capabilities`, failing
closed if the running service has a different value. Restart/recreate the
service to compare batch sizes, for example:

```powershell
$env:LLM_WIKI_EMBEDDING_BATCH_SIZE='16'
# restart/recreate the Embedding Service container, then run:
.venv\Scripts\python.exe scripts\benchmark_multimodal.py `
  --backend remote --service-url http://127.0.0.1:8790 `
  --allowed-host 127.0.0.1 --service-batch-size 16 `
  --items 16 --batch-size 16 --repeats 3
```

Start with service batch size `1` on an 8 GB GPU and increase it only after
observing peak memory and latency.

For a remote benchmark, the device is selected when the Embedding Service
starts, not by the benchmark client. Set `LLM_WIKI_EMBEDDING_DEVICE=cpu`
or `cuda` and the matching `LLM_WIKI_EMBEDDING_TORCH_BACKEND` there. A
verified Windows CUDA reference run on an RTX 3080 Laptop GPU at 1024
dimensions, `batch_size=1`, `items=4`, `warmup=1`, and three measured repeats
reported median latency of 110 ms for one image, 449 ms for four images, 79 ms
for one text item, 273 ms for four text items, and 773 ms for eight mixed
text/image units. Treat those as a smoke reference only: model cache state,
driver, hardware, and service transport affect results.

### Experimental vLLM comparison

Use vLLM only as a separate GPU comparison target. It requires a pinned image
digest, an immutable Qwen revision, and a private allowlisted endpoint:

```bash
python scripts/benchmark_multimodal.py \
  --backend vllm \
  --vllm-url http://embedding:8000 \
  --vllm-token "$LLM_WIKI_EMBEDDING_VLLM_TOKEN" \
  --vllm-image-digest 'vllm/vllm-openai@sha256:<64-hex-digest>' \
  --vllm-allowed-host embedding \
  --service-model-revision "$LLM_WIKI_MULTIMODAL_MODEL_REVISION" \
  --items 16 --batch-size 16 --repeats 3
```

This measures the direct vLLM adapter but does not migrate data or change the
default backend. Compare its report with the Transformers service; different
vectors are expected because vLLM documents a different Qwen3-VL image
preprocessing path. Promote it only after explicit review of the contract,
GPU, and labeled retrieval results.

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

## Recipe 11: Multimodal Memory Agent With OTel

For a local PostgreSQL-backed memory agent with Qwen3-VL embeddings and a
Grafana OTEL-LGTM viewer, either use the checked-in example overlay or generate
the equivalent full Compose file from explicit options:

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD and LLM_WIKI_API_TOKEN in .env first.
docker compose -f compose.yml -f compose.multimodal.yml \
  -f compose.memory-agent.yml up --build
```

The generator combination is:

```bash
python -m kogwistar_llm_wiki compose generate \
  --output compose.memory-agent.yml \
  --backend postgres --mode gpu \
  --with-otel --with-oauth --auth-mode static_token \
  --model-revision <immutable-Qwen3-VL-revision>
```

There is no special memory-agent generator mode. A standard deployment is the
same command with OTel/OAuth disabled and authentication set to `disabled`.

The base `postgres`, `rest`, and `mcp` services provide the memory-agent
interfaces. `compose.multimodal.yml` adds the private Qwen3-VL embedding
service, while `compose.memory-agent.yml` enables the OTel sink and exposes
Grafana on `http://127.0.0.1:3000`. The application traces are disabled or
enabled with `LLM_WIKI_OTEL_ENABLED`; the Settings panel can toggle the sink
for the current process after confirmation, but it does not start or stop
Docker services.

The example contains a commented Keycloak OAuth/OIDC service. Uncommenting it
only starts the identity provider; it does not change application auth. To use
it, configure `LLM_WIKI_AUTH_MODE=kogwistar_jwt`, issuer/audience, and a
compatible verification key, then recreate the app containers and verify
workspace ACLs. The UI reports OAuth/OIDC as deployment-managed and provides
no live toggle, because changing authentication without a restart could
expose the service or invalidate active sessions. For a trusted personal
deployment, use `LLM_WIKI_AUTH_MODE=disabled` instead.

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
