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
and representation-service health. Model and embedding profile changes are not
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

## Multimodal Representation Service

Production multimodal inference is a separate service so the REST and MCP
images remain lightweight and Torch-free. GPU is the recommended deployment
for practical Qwen3-VL vision inference:

```bash
LLM_WIKI_REPRESENTATION_TORCH_BACKEND=cu128 \
docker compose -f compose.yml -f compose.multimodal.yml \
  -f compose.representation-cuda.yml up --build
```

For CPU-only smoke tests or hosts without NVIDIA Container Toolkit, use the
explicit CPU fallback:

```bash
docker compose -f compose.yml -f compose.multimodal.yml \
  up --build
```

PowerShell:

```powershell
$env:LLM_WIKI_REPRESENTATION_TORCH_BACKEND = "cu128"
docker compose -f compose.yml -f compose.multimodal.yml `
  -f compose.representation-cuda.yml up --build
```

CPU fallback in PowerShell:

```powershell
$env:LLM_WIKI_REPRESENTATION_TORCH_BACKEND = "cpu"
docker compose -f compose.yml -f compose.multimodal.yml up --build
```

The sidecar exposes `/healthz`, `/readyz`, `/v1/capabilities`, and
`/v1/represent` only on the private Compose network. Configure the model,
revision, dimension, token, and Hugging Face cache through
`LLM_WIKI_REPRESENTATION_*`. The default is
`Qwen/Qwen3-VL-Embedding-2B` at 1024 dimensions. A remote deployment must use
authenticated HTTPS and an explicit `LLM_WIKI_REPRESENTATION_SERVICE_ALLOWED_HOSTS`
network allowlist.

Wait for the representation sidecar's `/readyz` endpoint before submitting
Stage 2 projection work. `/healthz` only confirms that the HTTP process is
alive; `/readyz` additionally confirms that Torch, the selected device, model,
and 1024-dimensional profile loaded successfully. Use the profile-matched
remote benchmark recipe in the [cookbook](cookbook.md#benchmark-multimodal-encoding)
after deployment. The benchmark client does not select CPU or CUDA; those are
service startup settings.

The base image intentionally remains Torch-free. Do not install a local model
into the REST/MCP image for production. If local adapter testing is needed,
follow the developer-only instructions in the cookbook. Qwen3-VL supports
64..2048 dimensions; 1536 is suitable for standard pgvector HNSW, while 2048
requires Chroma or non-HNSW storage. ColQwen is an explicit legacy
late-interaction route and must not share the dense service profile.

The representation image is a separate distribution boundary: it installs
`llm-wiki-representation-contract` and `llm-wiki-representation-service`, not
the application, parser, sink, Kogwistar, database, or MCP packages. Set
`LLM_WIKI_REPRESENTATION_MODEL_REVISION` to an immutable Hugging Face revision;
the sidecar refuses to start with a missing or floating revision. The model is
loaded asynchronously during startup, so `/healthz` can be live while `/readyz`
remains `503` until the profile-pinned model is ready.

## OpenTelemetry And Grafana

For a complete local memory-agent example combining PostgreSQL, the private
Qwen3-VL representation service, OTel, and Grafana, use
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

`docker compose down` stops containers but preserves named volumes.
`docker compose down -v` removes the database and app-data volumes and is the
explicit destructive reset. Export or inspect the graph before using it.

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
- `compose.multimodal.yml` is an optional overlay for the Qwen3-VL service.
- `compose.memory-agent.yml` is an optional overlay for OTel/Grafana and the
  memory-agent authentication example.

Do not run an overlay alone. Compose does not have a built-in way for an
overlay to require its base file, so the operator must use the documented
`-f` order. The CLI generator instead writes one self-contained YAML file.
