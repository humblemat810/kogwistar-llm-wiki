# Kogwistar LLM-Wiki

A **continuously-learning knowledge system** built on [Kogwistar](https://github.com/humblemat810/kogwistar).

Feed it documents. It extracts entities, promotes knowledge, distills wisdom, and projects an interlinked Obsidian vault — automatically, in the background.

```
raw sources → kg-doc-parser → conversation graph → promote → knowledge graph → Obsidian vault
                                                                               → wisdom engine
```

---

## What it is

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
