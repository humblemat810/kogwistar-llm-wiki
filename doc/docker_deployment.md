# Docker Deployment

The repository ships one application image and a Compose stack with separate
REST and MCP containers. Both containers use the same Postgres/pgvector
database and application-data volume, so they are loosely coupled at the
process boundary while sharing durable graph state.

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
`host.docker.internal`. For Azure/OpenAI, set the corresponding provider,
model, endpoint, and API-key environment variables without putting secrets in
the image.

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
is backend-independent; Postgres additionally needs the dimension for its
typed vector columns, while Chroma validates the dimension when its collection
is opened or written.
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

## Chroma Note

The Compose stack deliberately uses Postgres/pgvector for the two-service
default. Embedded Chroma is suitable for a single application container or a
development demo, but two independent containers must not concurrently share
an embedded Chroma directory. Use separate Compose projects or a shared
server-backed store when process isolation is required.

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
