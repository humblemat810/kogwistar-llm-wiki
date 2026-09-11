# Quickstart - LLM-Wiki

This tutorial starts with the safest default for this repo:
an ephemeral, one-process demo that ingests a document, runs maintenance,
projects the Obsidian vault, and exits.

That default matters because local embedded Chroma is process-unsafe for a
multi-process demo. So the quickstart does not pretend separate local processes
sharing one embedded Chroma path are the normal case.

The vault is still persisted on disk. The ephemeral part is only the graph/job
engine that lives inside the one demo process.

Optional shared-backend equivalents are included later:

- default quickstart: in-memory, one-process end-to-end demo
- optional shared ChromaDB backend: slower, explicit shared deployment
- optional PostgreSQL/pgvector backend: slower still, heavier shared deployment

For task-oriented recipes covering article collections, agents, authentication,
embeddings, archives, Docker, and Codex, see the [LLM-Wiki Cookbook](doc/cookbook.md).

---

## Prerequisites

- Python >= 3.11
- Git, Git Bash on Windows, or WSL for the bootstrap script
- An empty local directory for the Obsidian vault

---

## 0. Copy-paste demo config

If you just want to hit the ground running, start with this self-contained
demo. It creates a starter source file, uses one workspace, and writes the
vault into a known local directory.

This is the fast path. It keeps ingest, maintenance, and projection in one
process and writes the resulting vault to disk.

```bash
workspace="demo"
demo_root="logs/llm_wiki_demo"
source="$demo_root/my_document.md"
vault_dir="$demo_root/vault"

mkdir -p "$demo_root" "$vault_dir"
cat > "$source" <<'EOF'
# My Document

This is a starter document for the LLM-Wiki quickstart.

## Contacts
- Alice
- Bob
EOF

llm-wiki demo --workspace "$workspace" --source "$source" --vault "$vault_dir" --title "My Document" --source-format markdown --promotion-mode sync
```

After the run, open `logs/llm_wiki_demo/vault` in Obsidian.
Use `--parser-lane workflow_layered` on the same command if you want the
iterative layerwise parser path instead of the page-index parser.

The VS Code launch presets also read `.env` automatically and only ask you to
pick the parser lane:
- `Demo: Ollama (gemma4:e2b)`
- `Demo: Azure OpenAI (GPT-4o)`
- `Demo: Azure OpenAI (GPT-4.1)`
- `Demo: Azure OpenAI (pick model)`

### Optional backend equivalents

- In-memory demo: this is the default quickstart flow.
- ChromaDB shared backend: use the persistent `ingest` + `daemon` commands
  against an explicit shared Chroma deployment rather than local embedded
  Chroma.
- PostgreSQL/pgvector backend:
  ```bash
  llm-wiki --data-dir logs/llm_wiki_data --backend postgres --dsn postgresql://user:pass@localhost:5432/db ingest --workspace demo --source logs/llm_wiki_demo/my_document.md --title "My Document" --promotion-mode sync
  ```
  Then start the daemons with the same `--backend postgres --dsn ...` flags.

Those are the slower, shared-backend equivalents of the same workflow.

---

## 1. Bootstrap the environment

```bash
bash scripts/bootstrap-dev.sh
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows PowerShell
```

The script:

1. Clones sibling repos from GitHub if they are missing locally.
2. Installs those sibling checkouts editable.
3. Installs this package last.

After the script, local editable checkouts are what the venv uses.

---

## 2. Default demo: one process, one command

Use the `demo` command when you want the repo to be runnable immediately
without standing up a shared backend.

```bash
llm-wiki demo --workspace demo --source logs/llm_wiki_demo/my_document.md --vault logs/llm_wiki_demo/vault --title "My Document" --source-format markdown --promotion-mode sync
```

What this does:

- reads the source file
- builds an in-memory engine bundle for this one run
- ingests the document
- mirrors the parsed semantic tree into KG so the graph view is richer
- runs maintenance
- runs projection
- writes the Obsidian vault to disk
- exits after printing a JSON summary

Open `logs/llm_wiki_demo/vault` in Obsidian after it finishes.

---

## 3. Optional persistent flow: ingest a source document into the workspace

Use the persistent `llm-wiki ingest` bridge command only when you intentionally
want a shared backend flow.

```bash
llm-wiki --data-dir logs/llm_wiki_data ingest --workspace demo --source logs/llm_wiki_demo/my_document.md --title "My Document" --source-format markdown --promotion-mode sync
```

What this does:

- reads the source file
- writes parsed graph state into the workspace
- enqueues maintenance jobs
- enqueues projection jobs when promotion mode is `sync`

---

## 4. Knowledge-base article collection

For a durable collection of knowledge-base articles, use one stable workspace
and a shared backend. PostgreSQL/pgvector is the recommended path when the
ingest command and background workers run in separate processes. Keep the same
`--data-dir`, `--backend`, and `--dsn` for every command that belongs to that
workspace.

The CLI currently ingests one local article per invocation. This PowerShell
recipe is the supported batch composition; it intentionally does not invent a
separate bulk-write path:

```powershell
$workspace = "engineering-kb"
$dataDir = ".\\data"
$dsn = "postgresql://user:pass@localhost:5432/llm_wiki"

Get-ChildItem .\\articles -Recurse -File -Include *.md,*.txt |
  ForEach-Object {
    python -m kogwistar_llm_wiki `
      --data-dir $dataDir `
      --backend postgres `
      --dsn $dsn `
      ingest `
      --workspace $workspace `
      --source $_.FullName `
      --title $_.BaseName `
      --source-format markdown `
      --promotion-mode sync
  }
```

Use stable article paths and workspace IDs. To capture a changed local article,
run the same `ingest` command for that article again; the canonical source
lifecycle determines whether it represents a new revision. Agent clients use
the corresponding `reingest` capability when replacement text or an approved
HTTP(S) source is available.

For a production collection:

- Set parser and maintenance provider configuration before the batch. See
  [Real provider examples](#8-real-provider-examples).
- Keep sources and raw source artifacts available for provenance, revision
  handling, archive, and recovery.
- Run the maintenance and projection workers below after ingestion.
- Inspect the workspace with `llm-wiki report --workspace engineering-kb
  --data-dir .\\data --backend postgres --dsn $dsn`.
- Serve grounded API, A2A, or MCP access only after the workspace has usable
  knowledge. See [Agent Gateway Quickstart](doc/agent_gateway_quickstart.md).

There is not yet a dedicated manifest-driven `knowledge-base bootstrap`
command. The batch recipe above is deliberately transparent while that
operator feature remains separate work.

---

## 5. Optional persistent flow: run the background workers

Use the same `--data-dir` for both daemons so they operate on the same
workspace and persistent backend:

```bash
# Terminal 1
llm-wiki --data-dir logs/llm_wiki_data daemon maintenance --workspace demo --interval 10

# Terminal 2
llm-wiki --data-dir logs/llm_wiki_data daemon projection --workspace demo --vault logs/llm_wiki_data/vault --interval 5
```

Graceful wind-down:

- press `Ctrl-C` in the maintenance terminal
- press `Ctrl-C` in the projection terminal

Both daemons handle `Ctrl-C` cleanly.

---

## 6. Inspect the Obsidian vault

For the default demo, open `logs/llm_wiki_demo/vault` in Obsidian.

For the persistent flow, open `logs/llm_wiki_data/vault` in Obsidian.

You should see:

- one note per promoted entity
- wikilinks between related entities
- canvas files for entity clusters when the data produces them

---

## 7. Optional parser demo harness

`workflow-ingest demo --output-dir logs\workflow_ingest_demo` is a separate
demo harness from the `kg-doc-parser` repo. It writes parser/demo artifacts
only and does not populate the `llm-wiki` workspace.

---

## 8. Real provider examples

If you want to point parsing or maintenance at a real model, set the provider
and model explicitly. The repo accepts `azure_openai` as an env alias and
normalizes it to the `openai` chat provider.

Ollama:

```powershell
$env:KOGWISTAR_PARSER_PROVIDER='ollama'
$env:KOGWISTAR_PARSER_MODEL='gemma4:e2b'
$env:KOGWISTAR_PARSER_BASE_URL='http://localhost:11434'
$env:KOGWISTAR_MAINTENANCE_PROVIDER='ollama'
$env:KOGWISTAR_MAINTENANCE_MODEL='gemma4:e2b'
$env:KOGWISTAR_MAINTENANCE_BASE_URL='http://localhost:11434'
```

Azure OpenAI GPT-4o:

```powershell
$env:KOGWISTAR_PARSER_PROVIDER='azure_openai'
$env:KOGWISTAR_PARSER_MODEL='gpt4o'
$env:KOGWISTAR_PARSER_BASE_URL='https://<your-resource>.openai.azure.com/'
$env:KOGWISTAR_PARSER_API_KEY_ENV='OPENAI_API_KEY_GPT4O'
$env:KOGWISTAR_MAINTENANCE_PROVIDER='azure_openai'
$env:KOGWISTAR_MAINTENANCE_MODEL='gpt4o'
$env:KOGWISTAR_MAINTENANCE_BASE_URL='https://<your-resource>.openai.azure.com/'
$env:KOGWISTAR_MAINTENANCE_API_KEY_ENV='OPENAI_API_KEY_GPT4O'
```

Azure OpenAI GPT-4.1:

```powershell
$env:KOGWISTAR_PARSER_PROVIDER='azure_openai'
$env:KOGWISTAR_PARSER_MODEL='gpt41'
$env:KOGWISTAR_PARSER_BASE_URL='https://<your-resource>.openai.azure.com/'
$env:KOGWISTAR_PARSER_API_KEY_ENV='OPENAI_API_KEY_GPT4_1'
$env:KOGWISTAR_MAINTENANCE_PROVIDER='azure_openai'
$env:KOGWISTAR_MAINTENANCE_MODEL='gpt41'
$env:KOGWISTAR_MAINTENANCE_BASE_URL='https://<your-resource>.openai.azure.com/'
$env:KOGWISTAR_MAINTENANCE_API_KEY_ENV='OPENAI_API_KEY_GPT4_1'
```

---

## 9. Run the test suite

```bash
pytest tests/unit/          # fast unit tests, no external services needed
pytest -m integration       # Obsidian vault + other on-disk integration checks
pytest -m manual            # opt-in smoke cases that need local services like Ollama
```

After a successful run, the useful checks are:

- maintenance jobs in the durable meta-store should be `DONE`
- projection jobs in the durable meta-store should be `DONE`
- the projection manifest row should be `ready`
- the Obsidian vault should contain the expected `.md` files

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: kogwistar` | Run `bash scripts/bootstrap-dev.sh` |
| `ImportError: kogwistar-obsidian-sink` | Bootstrap installs it editable |
| Local edits to `kogwistar/` not reflected | Confirm `pip show kogwistar` points to the local path |
| No vault after the default demo | Confirm you ran `llm-wiki demo ...`, not the persistent `ingest` command by itself |
| Multi-process local Chroma behaves strangely | Do not use embedded local Chroma as the shared demo backend; use the one-process `demo` command, shared ChromaDB, or PostgreSQL |
| `PydanticDeprecatedSince20: min_items` warnings | They come from the installed `kogwistar` package; safe to ignore |
