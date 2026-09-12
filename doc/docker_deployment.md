# Docker Deployment

The repository ships one application image and a Compose stack with separate
REST and MCP containers. Both containers use the same Postgres/pgvector
database and application-data volume, so they are loosely coupled at the
process boundary while sharing durable graph state.

For copy-paste workflows from local article ingestion through agent serving,
health checks, embedding validation, and archive recovery, see the
[LLM-Wiki Cookbook](cookbook.md).

## Start

Set a development password first, then build and start the stack:

```bash
export POSTGRES_PASSWORD='change-this-development-password'
docker compose up --build
```

The default development endpoints are bound to loopback:

- REST/workbench: `http://127.0.0.1:8765`
- MCP Streamable HTTP: `http://127.0.0.1:8780/mcp`
- REST health: `http://127.0.0.1:8765/healthz`

The workbench **Settings** panel is an operator view over the same service.
It reports effective and staged desired settings, backend/profile compatibility,
and embedding-service health. Model and embedding profile changes are not
live edits: review the restart and re-embedding impact before applying them.
The panel never returns tokens or secret environment values.
- REST readiness: `http://127.0.0.1:8765/readyz`
- API capabilities: `http://127.0.0.1:8765/api/capabilities`

REST agent protocol routes are disabled by default. Enable them explicitly for
local use with `LLM_WIKI_AGENT_API_ENABLED=true`. Bearer authentication is
available through `LLM_WIKI_API_TOKEN` and `LLM_WIKI_AUTH_REQUIRED=true`;
configure token scopes with `LLM_WIKI_API_TOKEN_SCOPES=read,write` or `admin`.
For a trusted single-user local container, set `LLM_WIKI_AUTH_MODE=disabled`
explicitly. This is a special no-identity mode: no bearer token or workspace
ACL is required, but the configured workspace ID still scopes the data. It is
not a named default user and must not be used on a shared or non-local bind.
Explicit `disabled` overrides token variables left in the environment.
The native MCP transport has its own equivalent boundary: set
`LLM_WIKI_MCP_AUTH_REQUIRED=true` and `LLM_WIKI_MCP_TOKEN`. If the MCP token is
omitted, it falls back to `LLM_WIKI_API_TOKEN`; `LLM_WIKI_MCP_TOKEN_SCOPES`
falls back to `LLM_WIKI_API_TOKEN_SCOPES`. Static tokens are intended for local
or private development deployments; use a terminating proxy or an OAuth-aware
FastMCP provider for production identity management.
The application agent extra pins the tested FastMCP release, and the Docker
image installs that extra from `pyproject.toml` so the Dockerfile does not carry
a second FastMCP version declaration.
For production identity, set `LLM_WIKI_AUTH_MODE=kogwistar_jwt`, `JWT_ALG`,
`JWT_SECRET` (and optional `JWT_ISS`/`JWT_AUD`), then provide workspace
membership through JWT `workspaces`/`workspace_ids` claims or
`LLM_WIKI_WORKSPACE_ACL_JSON`, for example
`{"alice":{"workspaces":{"demo":["read","write"]}}}`. JWT mode fails
closed for an unlisted workspace and passes verified claims into the Kogwistar
ACL context. Change environment variables with `docker compose up -d
--force-recreate`; Compose captures environment at container start.
Moving from personal mode to static-token or JWT mode is configuration-only if
the same workspace ID is retained: graph data does not need re-indexing. You
must provision token scopes and, for JWT mode, workspace membership before
clients can access it. Existing personal records have no retroactive principal
owner. Splitting data between users requires explicit application-level
export/import into separate workspace IDs.
For any non-local bind or production deployment, require authentication, TLS,
rate limiting, and network policy in front of both services.

Agent source fetching is disabled unless the host is explicitly allowlisted:
set `LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS=docs.example.test` and optionally
adjust `LLM_WIKI_SOURCE_FETCH_MAX_BYTES` and
`LLM_WIKI_SOURCE_FETCH_TIMEOUT_SECONDS`. The MCP boundary accepts only
HTTP(S) URLs for fetching and never accepts local filesystem paths.

The semantic MCP tools are `query`, `search`, `ingest`, `source`, `reingest`,
`maintain`, `status`, `hypergraph_search`, `history`, `propose`, and `confirm`.
Read-only tools use `read` scope; source capture, maintenance, and proposal
application use `write` scope. Internal queues, workers, databases, and direct
graph writes are intentionally not exposed.

## Isolation

Use a distinct Compose project and workspace for an isolated experiment:

```bash
COMPOSE_PROJECT_NAME=llm-wiki-exp-a LLM_WIKI_WORKSPACE=exp-a \
POSTGRES_PASSWORD='experiment-password' docker compose up --build
```

The Compose project name creates separate named volumes and a separate
Postgres container. Within one stack, `workspace_id` remains the application
namespace used by graph, conversation, workflow, and interaction history.
For stronger isolation, use a separate Compose project rather than relying on
workspace IDs alone.

To compare runs in the same Postgres instance, use separate workspace IDs and
separate application run/configuration fingerprints. Do not use a mutable
container-local path as the identity of an experiment.

## Provider Configuration

Compose passes parser and maintenance provider settings through from the shell
or `.env`. For Ollama on the host, the default URL uses
`host.docker.internal`. Generated bundles include an explicit
`host.docker.internal:host-gateway` mapping so this host-model configuration
also works on Linux, not only Docker Desktop. For Azure/OpenAI, set the corresponding provider,
model, endpoint, and API-key environment variables without putting secrets in
the image. If an `.env` secret contains `$`, write it as a single-quoted
literal, such as `LLM_WIKI_MCP_TOKEN='token-with-$-characters'`; otherwise
Compose may treat part of the token as a variable reference and alter it.

Embedding configuration is independent from parser/maintenance LLM
configuration. The fast deterministic embedder is used when no embedding
variables are set. For a real model, configure the application-owned names,
for example:

```bash
KOGWISTAR_LLM_WIKI_KNOWLEDGE_EMBED_PROVIDER=ollama
KOGWISTAR_LLM_WIKI_KNOWLEDGE_EMBED_MODEL=qwen3-embedding:0.6b
KOGWISTAR_LLM_WIKI_KNOWLEDGE_EMBED_BASE_URL=http://host.docker.internal:11434
KOGWISTAR_LLM_WIKI_KNOWLEDGE_EMBED_DIMENSION=1024
```

Use `CONVERSATION`, `WORKFLOW`, or `WISDOM` in place of `KNOWLEDGE` to scope
another graph space. A space-specific setting overrides the global
`KOGWISTAR_LLM_WIKI_EMBED_*` setting, which overrides `KOGWISTAR_EMBED_*` and
the parser-compatible `KG_DOC_EMBED_*` setting. Postgres requires the real
model output dimension to be declared; it will reject an ambiguous real-model
configuration rather than create an incompatible vector index. This contract
is backend-independent. Each persistent graph-space store records a sanitized
embedding profile in the Kogwistar metadata store. Chroma does not use mutable
collection metadata as the authority because reopening a collection can retain
old metadata silently.

The current LLM-Wiki PostgreSQL bundle stores all graph spaces in shared
physical pgvector tables. Consequently, every PostgreSQL graph space must use
the same embedding provider, model, dimension, and base URL. Per-space models
and dimensions remain valid for in-memory and Chroma bundles because those
spaces have separate physical stores. PostgreSQL refuses a mixed profile at
bootstrap instead of silently storing incomparable vectors in one HNSW index.

Changing the embedding dimension of an existing PostgreSQL store is a storage
migration, not a configuration-only change. Startup checks every `embedding`
column and fails before writes when it finds a stale `vector(N)` type. Stop
writers, archive canonical state, create an isolated target database or schema
with the new profile, replay/re-embed, validate, and then cut over. Do not run
`ALTER COLUMN ... TYPE vector(N)` on populated tables: old vectors and HNSW
indexes require rebuilding. See [PostgreSQL embedding migrations](postgres_embedding_migration.md).
The same rule applies to Chroma when the provider, model, endpoint, dimension,
or similarity metric changes. Startup rejects a populated Chroma directory
whose registered profile differs, including same-dimension model changes.
Legacy populated Chroma directories created before profile registration fail
closed until an operator verifies the old settings and explicitly runs:

```powershell
python -m kogwistar_llm_wiki --data-dir ./data --backend chroma `
  embeddings inspect --workspace demo
python -m kogwistar_llm_wiki --data-dir ./data --backend chroma `
  embeddings adopt-legacy-profile --workspace demo `
  --acknowledge-legacy-vectors
```

Adoption records an operator attestation; it does not re-embed or prove old
vectors. The safer migration is archive, restore into an isolated directory,
re-embed, validate, and cut over.
Both the REST and MCP containers construct all graph-space engines before
serving requests, so this validation happens during container startup. A
Compose configuration can therefore fail fast at startup instead of running
with only some vector spaces configured. `docker compose config --quiet`
still validates Compose interpolation; it cannot validate a model's actual
output dimension, so that dimension check remains an application startup
check.

The image also has a startup entrypoint that validates embedding configuration
before launching the requested CLI command. Invalid settings cause the app
container to exit with status `78`; the Postgres infrastructure container is
independent and may still start. This gate validates declared configuration,
not a live embedding request, so the model's actual output dimension must still
match the declared value.

## Host Cockpit Callback

The container does not include Codex or Claude Code. To let a host-side agent
drive Codex cockpit turns, set `LLM_WIKI_COCKPIT_CALLBACK_URL` and explicitly
allow its hostname with `LLM_WIKI_COCKPIT_CALLBACK_ALLOWED_HOSTS`. The callback
receives only the bounded cockpit request, current lens snapshot, and prior
observations. It must return a validated cockpit action. Set
`LLM_WIKI_COCKPIT_CALLBACK_TOKEN` when the host endpoint authenticates callers.
Keep this callback on the local host or a trusted private network.

## Direct Image Usage

The image contains the CLI and all three sibling repositories. Run the REST
service directly when Compose is not wanted:

```bash
docker run --rm -p 127.0.0.1:8765:8765 \
  -v llm-wiki-data:/var/lib/llm-wiki \
  -e LLM_WIKI_AGENT_API_ENABLED=false \
  kogwistar-llm-wiki:local \
  llm-wiki --data-dir /var/lib/llm-wiki --backend chroma \
  workbench --workspace default --host 0.0.0.0 --port 8765
```

The image default command is only a help display. Always provide an explicit
service command in deployments so REST and MCP lifecycles remain independently
supervisable.

## Multimodal Embedding Service

Production Qwen3-VL inference runs in a separate GPU embedding container. The
REST and MCP application image remains Torch-free. The recommended complete
stack is PostgreSQL plus the vLLM overlay; `compose.multimodal.yml` and
`compose.embedding-cuda.yml` are the legacy in-process Transformers path and
must not be combined with the vLLM overlay.

Copy `.env.example` to `.env` and set at least:

```dotenv
LLM_WIKI_IMAGE=profchan/kogwistar-llm-wiki:v0.3.3
POSTGRES_PASSWORD=change-this-development-password
LLM_WIKI_EMBEDDING_VLLM_IMAGE=vllm/vllm-openai@sha256:<pinned-64-hex-digest>
LLM_WIKI_EMBEDDING_VLLM_TOKEN=change-me
LLM_WIKI_MULTIMODAL_MODEL_REVISION=<immutable-40-character-Hugging-Face-commit>
LLM_WIKI_EMBEDDING_GPU_MEMORY_UTILIZATION=0.86
LLM_WIKI_EMBEDDING_MAX_MODEL_LEN=8192
LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET=7680
LLM_WIKI_EMBEDDING_VLLM_ENFORCE_EAGER=1
LLM_WIKI_EMBEDDING_VLLM_MAX_NUM_SEQS=1
```

For Windows Docker Desktop/WSL2, keep the compatibility setting below when
vLLM reports `UVA is not available`:

```dotenv
LLM_WIKI_EMBEDDING_VLLM_USE_V2_MODEL_RUNNER=0
```

Native Linux operators may set it to `1` only after validating UVA support.
This is a runtime capability setting, not an unconditional Windows/Linux
switch.
The safe-long profile uses `max_model_len=8192`, a `7680` token crop budget,
eager execution, and one scheduled sequence. The crop budget leaves room for
the instruction, image tokens, and scheduler overhead. On an 8 GiB GPU this
profile may still require a lower memory utilization value or a smaller model
length; vLLM must report healthy before it is used.

#### Long-context eager-mode experiment

On an RTX 3080 Laptop GPU with 8 GiB VRAM, the default compiled configuration
failed for `max_model_len=8192` because vLLM observed only `6.92 GiB` free and
`gpu_memory_utilization=0.92` requested `7.36 GiB`. A temporary experiment
using `8192`, `gpu_memory_utilization=0.86`, `--enforce-eager`, and
`--max-num-seqs 1` started successfully. It reported `1.08 GiB` of KV cache,
`10,096` cache tokens, and maximum concurrency of `1.23x` at 8,192 tokens.

`--enforce-eager` permits bounded concurrent requests but disables CUDA Graphs and `torch.compile`,
trading throughput for lower startup memory. `--max-num-seqs 1` is a separate
scheduler limit and was used only to make this constrained experiment fit; it
is not a general recommendation for batch throughput. A batch of four
8,192-token requests still cannot fit in this cache. The experiment was not
is the checked-in safe-long default. Use the Compose environment knobs above
to select a shorter context when the GPU cannot start this profile.

The current pinned vLLM image must also pass a live 1,024-dimensional output
probe. The Qwen3-VL model advertises Matryoshka dimensions, but some vLLM
builds reject the `dimensions=1024` request. Such a build remains blocked for
Stage 2 writes; the compatible Transformers embedding service is the reference
route until a pinned vLLM candidate passes the probe.

Start the complete GPU memory-agent stack from the repository root:

```powershell
docker compose -p llm-wiki-memory `
  -f compose.yml `
  -f compose.embedding-vllm.yml `
  -f compose.memory-agent.yml `
  up -d --no-build
```

The first start downloads the large vLLM image and then downloads the model
into the named `embedding_vllm_hf_cache` volume. The application image is
reused from `LLM_WIKI_IMAGE`; `--no-build` does not prevent image pulls. To
require local images after the initial pull, use:

```powershell
docker compose -p llm-wiki-memory `
  -f compose.yml `
  -f compose.embedding-vllm.yml `
  -f compose.memory-agent.yml `
  up -d --no-build --pull never
```

Inspect all services, including containers waiting on the embedding
healthcheck:

```powershell
docker compose -p llm-wiki-memory `
  -f compose.yml `
  -f compose.embedding-vllm.yml `
  -f compose.memory-agent.yml `
  ps -a
```

The embedding service must become `healthy` before REST and MCP start. Its
`/health` check only confirms the vLLM HTTP process; the Compose healthcheck
uses `python3` because the official image does not provide a `python` command.
The model-loading logs and health state can be inspected with:

```powershell
docker compose -p llm-wiki-memory `
  -f compose.yml `
  -f compose.embedding-vllm.yml `
  -f compose.memory-agent.yml `
  logs -f embedding
```

The sidecar is private to the Compose network. It serves the vLLM-compatible
embedding API on internal port `8000`; it is not published to the host. The
model revision is immutable, and `1024` is the recommended profile. Changing
the model, revision, dimension, metric, or preprocessing creates a distinct
embedding space and requires explicit re-embedding; vectors are never reused
merely because dimensions match.

To restart after changing `.env` or an overlay, recreate the affected
containers:

```powershell
docker compose -p llm-wiki-memory `
  -f compose.yml `
  -f compose.embedding-vllm.yml `
  -f compose.memory-agent.yml `
  up -d --no-build --pull never --force-recreate
```

The normal application endpoints are REST/workbench at
`http://127.0.0.1:8765`, MCP at `http://127.0.0.1:8780/mcp`, and Grafana at
`http://127.0.0.1:3000`. `/healthz` is a liveness check; `/readyz` confirms
that the application has completed its startup and profile validation.

### Experimental vLLM backend

The experimental vLLM route is GPU-only and is not combined with the
Transformers Embedding Service overlay. It calls vLLM's Qwen3-VL Chat
Embeddings API directly from LLM-Wiki, while the application retains the
Stage 1/Stage 2 profile and asset-safety checks. vLLM is a separate vector
space because its Qwen3-VL image preprocessing differs from the Transformers
path.

Set an immutable model revision, private vLLM token, and pinned image digest:

```bash
export LLM_WIKI_MULTIMODAL_MODEL_REVISION='<immutable-model-commit-sha>'
export LLM_WIKI_EMBEDDING_VLLM_TOKEN='<private-token>'
export LLM_WIKI_EMBEDDING_VLLM_IMAGE='vllm/vllm-openai@sha256:<64-hex-digest>'
docker compose -f compose.yml -f compose.embedding-vllm.yml up -d
```

The overlay reserves NVIDIA GPUs, exposes vLLM only inside the Compose
network, and stores model downloads in `embedding_vllm_hf_cache`. Do not also
apply `compose.multimodal.yml`; the two overlays define different embedding
backends. The app creates an isolated vLLM projection and never reuses
Transformers vectors. Production promotion requires explicit review of the
contract, GPU, and labeled retrieval comparison results.

## OpenTelemetry And Grafana

For a complete local memory-agent example combining PostgreSQL, the private
Qwen3-VL embedding service, OTel, and Grafana, use
[`compose.memory-agent.yml`](../compose.memory-agent.yml) as the final Compose
overlay. The same overlay contains a commented Keycloak example; see the
[cookbook recipe](cookbook.md#recipe-11-multimodal-memory-agent-with-otel) for
the required JWT/OIDC boundary and restart procedure.

Enable application tracing before starting the service:

```bash
LLM_WIKI_OTEL_ENABLED=true \
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
docker compose up -d
```

The workbench Settings panel shows whether tracing is enabled and whether an
OTLP endpoint is configured. Its toggle controls trace emission in the current
LLM-Wiki process after confirmation; it does not manage Grafana or the
collector container. Start and stop those deployment services separately, and
do not treat `/healthz` as proof that traces reached Grafana. The base image
continues to work without OTel packages; install the `otel` extra when export
is required.

## Chroma Note

The Compose stack deliberately uses Postgres/pgvector for the two-service
default. Embedded Chroma is suitable for a single application container or a
development demo, but two independent containers must not concurrently share
an embedded Chroma directory. Use separate Compose projects or a shared
server-backed store when process isolation is required.
Persistent Chroma profiles are bound per graph-space directory, so separate
conversation, workflow, knowledge, and wisdom directories may use different
embedding models and dimensions. They must not be mixed inside one physical
directory. Use `embeddings inspect` to see configured, registered, and physical
state without binding a new profile.

The Compose generator does not emit embedded Chroma bundles because REST and
MCP would be separate writers of one local directory. Use PostgreSQL for
multi-process Compose, or run the single-process `demo` command when embedded
Chroma is required.

## Shutdown And Persistence

For the layered GPU memory-agent stack, stop it with the same project and
overlay files used to start it:

```powershell
docker compose -p llm-wiki-memory `
  -f compose.yml `
  -f compose.embedding-vllm.yml `
  -f compose.memory-agent.yml `
  down
```

This stops containers but preserves named volumes. Adding `-v` removes the
database, model-cache, and app-data volumes and is an explicit destructive
reset. Export or inspect the graph before using it.

## Archive And Recovery

LLM-Wiki has an operator-only portable archive command. It captures the
authoritative Kogwistar event history by per-namespace sequence watermark and
can rebuild Chroma vectors and other derived indexes after restore. It is not
exposed through MCP or REST.

Stop or drain application writers before capture. Never copy an actively
written Chroma directory. Run the archive command as a one-off process with
the data volume mounted:

```powershell
docker compose stop llm-wiki
docker compose run --rm llm-wiki python -m kogwistar_llm_wiki `
  --data-dir /var/lib/llm-wiki --backend chroma `
  archive create --workspace demo --output /var/lib/llm-wiki-archives/demo.tar.gz `
  --include-backend-snapshot
```

Verify before storing or transferring the archive:

```powershell
docker compose run --rm llm-wiki python -m kogwistar_llm_wiki `
  archive verify --archive /var/lib/llm-wiki-archives/demo.tar.gz
```

Restore is dry-run by default and must target a fresh isolated data directory.
Use `--apply` only after validation. Omitting `--target-workspace` performs an
exact restore of the original workspace ID into that isolated datastore;
specifying it performs a typed workspace remap and rebuilds derived vectors and
indexes. Portable event archives are the migration format. Backend snapshots
are optional exact-recovery accelerators and require matching backend and
embedding fingerprints; use `--use-backend-snapshot` only for a compatible
quiescent base archive. Archive files can contain source text and history, so
protect them with the deployment's filesystem, backup, transport, or KMS
encryption controls.
### Compose file composition

The checked-in Compose files are intentionally layered, not independent
applications:

- `compose.yml` is the required base and defines PostgreSQL, REST, MCP, and
  persistent volumes.
- `compose.embedding-vllm.yml` is the recommended GPU Qwen3-VL overlay.
- `compose.multimodal.yml` is the legacy in-process Transformers overlay for
  explicit CPU/reference use and must not be combined with vLLM.
- `compose.embedding-cuda.yml` modifies the legacy Transformers overlay for
  CUDA and is not used by the vLLM stack.
- `compose.memory-agent.yml` is an optional overlay for OTel/Grafana and the
  memory-agent authentication example.

Prebuilt public images can be published and consumed through the
[`Docker Hub publishing guide`](docker_hub_publishing.md). The normal REST/MCP
image is Torch-free; Qwen3-VL inference remains a separate CPU or GPU sidecar.

The application and standalone embedding Dockerfiles use multi-stage builds.
Rust, Cargo, GCC, Maturin, and package source checkouts exist only in builder
stages. BuildKit cache mounts reuse Python and Rust downloads between builds,
while the final runtime stages contain only the installed runtime environment.
Model checkpoints are never baked into either image; keep Hugging Face caches
in the Compose volume. Application tags publish only the application image,
and embedding images use the explicit targets documented in
[`Docker Hub publishing`](docker_hub_publishing.md).

Do not run an overlay alone. Compose does not have a built-in way for an
overlay to require its base file, so the operator must use the documented
`-f` order. The CLI generator instead writes one self-contained YAML file.
