# Kogwistar LLM-Wiki

A **continuously-learning knowledge system** built on [Kogwistar](https://github.com/humblemat810/kogwistar).

Feed it documents. It extracts entities, promotes knowledge, distills wisdom, and projects an interlinked Obsidian vault — automatically, in the background.

```
raw sources → kg-doc-parser → conversation graph → promote → knowledge graph → Obsidian vault
                                                                               → wisdom engine
```

---

## What it is

Maintenance currently has two distinct outputs:
- `derived_knowledge` for cross-document label-merge synthesis, stored under a separate derived-KG namespace
- `execution_wisdom` for reusable lessons derived from workflow failures and repeated maintenance outcomes

| Layer | Role |
|---|---|
| **conversation** | Working memory — parsed artifacts, candidate links, maintenance jobs |
| **knowledge graph** | Stabilized, promoted truth |
| **wisdom engine** | Reusable patterns derived from execution history |
| **Obsidian vault** | Human-facing projection (markdown + canvas) |

> It is **not** a chatbot, a note app, or a RAG wrapper.

---

## Installation

### Option A — Local development (recommended)

```bash
bash scripts/bootstrap-dev.sh
```

The script:
1. Clones `kogwistar`, `kogwistar-obsidian-sink`, `kg-doc-parser` from GitHub if not already present locally
2. Installs all three as **editable** from the local checkout
3. Installs this package last

After running, the venv always uses local editable sources — re-running is safe (existing checkouts are kept).

### Option B — GitHub-only (CI / no local edits needed)

For cross-repo architectural refactors, those sibling repos are intentionally
editable from the same project root. Use the documented repo boundaries to
decide where a concept belongs, but do not treat the sibling checkouts as
read-only while capability and policy are being moved to their proper homes.

```bash
pip install git+https://github.com/humblemat810/kogwistar.git
pip install git+https://github.com/humblemat810/kogwistar-obsidian-sink.git
pip install git+https://github.com/humblemat810/kg-doc-parser.git
pip install -e ".[dev]"
```

> `kogwistar` and `kogwistar-obsidian-sink` are **not on PyPI** — both options install them from source.

> **Windows**: Run the bootstrap from Git Bash or WSL.

---

## Quick demo

The default demo stays in one process on purpose. It uses the in-memory engine
bundle for the graph/job state, then writes the Obsidian vault to disk before
exiting.

That is the safest local quickstart because embedded local Chroma is
process-unsafe for a multi-process demo.

The demo also mirrors the parsed semantic tree into KG so the graph view shows
more than a single promoted node.

```bash
# 1. Bootstrap (first time only)
bash scripts/bootstrap-dev.sh

# 2. Create a small demo source
mkdir -p logs/llm_wiki_demo/vault
cat > logs/llm_wiki_demo/my_document.md <<'EOF'
# My Document

This is a starter document for the LLM-Wiki quickstart.

## Contacts
- Alice
- Bob
EOF

# 3. Run the one-process demo
llm-wiki demo --workspace demo --source logs/llm_wiki_demo/my_document.md --vault logs/llm_wiki_demo/vault --title "My Document" --source-format markdown --promotion-mode sync
```

Then open `logs/llm_wiki_demo/vault` in Obsidian.

For the agent gateway and optional OpenTelemetry contract, see
[`doc/adr_agent_gateway_and_otel.md`](doc/adr_agent_gateway_and_otel.md).
Agent protocol routes are opt-in with `LLM_WIKI_AGENT_API_ENABLED=true`; keep
them behind authentication when binding beyond localhost. Install optional
support with `pip install -e ".[agent,otel]"`.

To try the iterative layerwise parser path, add `--parser-lane workflow_layered`
to the same command and keep the same provider/model settings.

For isolated REST and MCP serving in containers, see
[`doc/docker_deployment.md`](doc/docker_deployment.md). The Compose stack runs
separate REST and MCP containers against Postgres/pgvector with persistent
named volumes and workspace isolation.

If you use VS Code, the launch presets already read `.env` and only prompt for
the parser lane:
- `Demo: Ollama (gemma4:e2b)`
- `Demo: Azure OpenAI (GPT-4o)`
- `Demo: Azure OpenAI (GPT-4.1)`
- `Demo: Azure OpenAI (pick model)`

### Real provider settings

The repo keeps secrets in `.env`, and `.env.example` is only a placeholder template.
When you need a specific provider/model pair, set the parser and maintenance
variables explicitly.

Ollama example:

```bash
KOGWISTAR_PARSER_PROVIDER=ollama
KOGWISTAR_PARSER_MODEL=gemma4:e2b
KOGWISTAR_PARSER_BASE_URL=http://localhost:11434

KOGWISTAR_MAINTENANCE_PROVIDER=ollama
KOGWISTAR_MAINTENANCE_MODEL=gemma4:e2b
KOGWISTAR_MAINTENANCE_BASE_URL=http://localhost:11434
```

### Embedding settings

The llm-wiki engine builders use a small deterministic `tiny`-style embedder
when no embedding settings are present. This is intentional for demos and
tests. To use a real embedding model, configure the shared parser provider
factory with `KG_DOC_EMBED_*` settings before starting the app:

```bash
KG_DOC_EMBED_PROVIDER=ollama
KG_DOC_EMBED_MODEL=qwen3-embedding:0.6b
KG_DOC_EMBED_BASE_URL=http://localhost:11434
# Set this to the model's actual output dimension, especially for Postgres.
KG_DOC_EMBED_DIMENSION=1024
```

The same settings are honored by the in-memory, persistent, and Postgres
namespace builders. For application-owned deployments, prefer the
`KOGWISTAR_LLM_WIKI_EMBED_*` names; they override `KOGWISTAR_EMBED_*`, which in
turn overrides the parser compatibility names above. Code callers can instead
pass `embedding_config` or the explicit `embedding_provider`,
`embedding_model`, and `embedding_dimension` arguments. For every backend, a
real provider without a declared dimension is rejected before engine
initialization rather than silently creating an incompatible 2D vector index;
Postgres additionally uses the dimension for its typed vector columns. Changing the model or dimension for
an existing persistent store requires a separate store or an intentional
migration.

Embedding settings may also be scoped to a graph space by inserting the space
name before `EMBED`, for example:

```bash
KOGWISTAR_LLM_WIKI_CONVERSATION_EMBED_PROVIDER=ollama
KOGWISTAR_LLM_WIKI_CONVERSATION_EMBED_MODEL=nomic-embed-text
KOGWISTAR_LLM_WIKI_KNOWLEDGE_EMBED_PROVIDER=ollama
KOGWISTAR_LLM_WIKI_KNOWLEDGE_EMBED_MODEL=qwen3-embedding:0.6b
KOGWISTAR_LLM_WIKI_KNOWLEDGE_EMBED_DIMENSION=1024
KOGWISTAR_LLM_WIKI_WORKFLOW_EMBED_PROVIDER=fake
```

The spaces are `conversation` (all foreground/background interaction history),
`workflow` (runtime and maintenance state), `knowledge` (durable KG), and
`wisdom`. An unset space inherits the global app setting.
`derived_knowledge` uses the knowledge embedder by design. The maintenance
worker's chat model is independently configured with `KOGWISTAR_MAINTENANCE_*`,
but its conversation replies still use the shared `conversation` embedding
space. A separate maintenance-self conversation embedder is not currently a
supported configuration; adding one would require a new physical graph space
and persistence contract.

Azure OpenAI example using GPT-4o:

```bash
KOGWISTAR_PARSER_PROVIDER=azure_openai
KOGWISTAR_PARSER_MODEL=gpt4o
KOGWISTAR_PARSER_BASE_URL=https://<your-resource>.openai.azure.com/
KOGWISTAR_PARSER_API_KEY_ENV=OPENAI_API_KEY_GPT4O

KOGWISTAR_MAINTENANCE_PROVIDER=azure_openai
KOGWISTAR_MAINTENANCE_MODEL=gpt4o
KOGWISTAR_MAINTENANCE_BASE_URL=https://<your-resource>.openai.azure.com/
KOGWISTAR_MAINTENANCE_API_KEY_ENV=OPENAI_API_KEY_GPT4O
```

Azure OpenAI example using GPT-4.1:

```bash
KOGWISTAR_PARSER_PROVIDER=azure_openai
KOGWISTAR_PARSER_MODEL=gpt41
KOGWISTAR_PARSER_BASE_URL=https://<your-resource>.openai.azure.com/
KOGWISTAR_PARSER_API_KEY_ENV=OPENAI_API_KEY_GPT4_1

KOGWISTAR_MAINTENANCE_PROVIDER=azure_openai
KOGWISTAR_MAINTENANCE_MODEL=gpt41
KOGWISTAR_MAINTENANCE_BASE_URL=https://<your-resource>.openai.azure.com/
KOGWISTAR_MAINTENANCE_API_KEY_ENV=OPENAI_API_KEY_GPT4_1
```

The provider alias `azure_openai` is normalized to the `azure` chat provider
used by `kg-doc-parser`, while the model field carries the Azure deployment
name. Regular OpenAI still uses the `openai` provider.

Optional slower equivalents:

- In-memory, one-process demo: this is the default quick demo.
- ChromaDB shared backend: use the persistent `ingest` + `daemon` flow against
  an explicit shared Chroma deployment.
- PostgreSQL/pgvector backend:
  `llm-wiki --data-dir logs/llm_wiki_data --backend postgres --dsn postgresql://user:pass@localhost:5432/db ingest --workspace demo --source logs/llm_wiki_demo/my_document.md --title "My Document" --source-format markdown --promotion-mode sync`
  and then run the same `daemon` commands with the same backend flags.
- Embedded local Chroma: not the default demo path, because multiple local
  processes sharing that path are not the safe story.

See [QUICKSTART.md](QUICKSTART.md) for the full step-by-step tutorial.

---

## CLI reference

```
llm-wiki demo --workspace <id> --source <path> --vault <path> [--title <text>] [--promotion-mode sync|pending]
llm-wiki [--backend chroma|postgres --dsn <postgres-dsn>] ingest --workspace <id> --source <path> [--title <text>] [--promotion-mode sync|pending]
llm-wiki [--backend chroma|postgres --dsn <postgres-dsn>] daemon projection  --workspace <id> --vault <path> [--interval <s>]
llm-wiki [--backend chroma|postgres --dsn <postgres-dsn>] daemon maintenance --workspace <id>                [--interval <s>]
```

Full reference: [doc/cli_reference.md](doc/cli_reference.md)

---

## Docs

### System Documentation

| Document | Purpose |
|---|---|
| [QUICKSTART.md](QUICKSTART.md) | Step-by-step tutorial |
| [doc/cli_reference.md](doc/cli_reference.md) | CLI cheatsheet |
| [doc/diagrams.md](doc/diagrams.md) | CLI spider map, pipeline, algorithm & data-flow diagrams |
| [doc/architecture.md](doc/architecture.md) | System design |
| [doc/core_workflows.md](doc/core_workflows.md) | Workflow graph designs |
| [doc/adr_conversation_two_stage_materialization.md](doc/adr_conversation_two_stage_materialization.md) | Optional two-stage conversation materialization, ownership boundaries, and benchmark contract |
| [doc/agent_gateway_quickstart.md](doc/agent_gateway_quickstart.md) | Agent integration quickstart for OpenAI-shaped, A2A, MCP, and OTel usage |
| [doc/adr_agent_gateway_and_otel.md](doc/adr_agent_gateway_and_otel.md) | Agent gateway architecture, safety boundaries, and optional OpenTelemetry |
| [skills/llm-wiki-knowledge/SKILL.md](skills/llm-wiki-knowledge/SKILL.md) | Portable grounded knowledge-management and proposal-review instructions for compatible agents |
| [doc/implementation_plan_observability_staged_materialization_goal_mode.md](doc/implementation_plan_observability_staged_materialization_goal_mode.md) | Historical dependency-order reference and remaining goal-mode delivery gates |
| [doc/testing_guide.md](doc/testing_guide.md) | Local test-running pitfalls and pytest cache guidance |
| [doc/lane_namespace_convention.md](doc/lane_namespace_convention.md) | Namespace/lane conventions |
| [doc/maintenance_job_taxonomy.md](doc/maintenance_job_taxonomy.md) | Maintenance job types |
| [doc/glossary.md](doc/glossary.md) | Term definitions |
| [doc/distillation_core_migration.md](doc/distillation_core_migration.md) | Notes on migrating distillation to kogwistar core |
| [STATUS.md](STATUS.md) | Implementation status |

### Ecosystem Analysis

| Document | Purpose |
|---|---|
| [doc/engineering_assessment.md](doc/engineering_assessment.md) | Engineering-level assessment of the author and ecosystem |
| [doc/ai_os_gap_analysis.md](doc/ai_os_gap_analysis.md) | Gap analysis: what is missing to become a genuine AI-native OS |
| [doc/ai_os_roadmap.md](doc/ai_os_roadmap.md) | Executable plan: polish to production + AI OS build order |

---

## Development

```bash
pytest tests/unit/          # fast unit tests (in-memory, no services)
pytest -m integration       # Obsidian vault + other on-disk integration checks
pytest -m manual            # opt-in smoke tests requiring local services
```

After a successful ingest/projection run, the most useful manual checks are:
- maintenance jobs in the durable meta-store should be `DONE`
- projection jobs in the durable meta-store should be `DONE`
- the projection manifest row should be `ready`
- the Obsidian vault should contain the expected `.md` files
