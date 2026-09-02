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

REST agent protocol routes are disabled by default. Enable them explicitly for
local use with `LLM_WIKI_AGENT_API_ENABLED=true`. For any non-local bind or
production deployment, put authentication, TLS, rate limiting, and network
policy in front of both services.

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
