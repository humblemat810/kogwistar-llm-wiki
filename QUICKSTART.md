# Quickstart — LLM-Wiki

This tutorial walks you through a full end-to-end run: ingest a document, promote knowledge, run the background workers, and inspect the Obsidian output.

---

## Prerequisites

- Python ≥ 3.11
- Git, git-bash (Windows) or WSL for the bootstrap script
- An [Obsidian](https://obsidian.md) vault (any empty directory works)

---

## Step 1 — Bootstrap the environment

```bash
bash scripts/bootstrap-dev.sh
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows PowerShell
```

The script runs in three steps:

1. **Clone** each sibling repo from GitHub if its local directory is missing; keep it if already present.
2. **Install editable** from the local directory (always, even if it was just cloned).
3. **Install this package** last — after all siblings are in the env.

| Sibling repo | Local dir exists? | Action |
|---|---|---|
| `kogwistar` | ✅ already cloned | keep, reinstall editable |
| `kogwistar` | ❌ missing | clone from GitHub, then install editable |
| `kogwistar-obsidian-sink` | ✅ / ❌ | same |
| `kg-doc-parser` | ✅ / ❌ | same |

After the script, **local editable checkouts are always what the venv uses** — regardless of any prior `pip install git+...` that may have been done.

> **Windows**: Run from Git Bash or WSL.

---

## Step 2 — Ingest a document

```python
# ingest_demo.py
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline

pipeline = IngestPipeline(workspace_id="demo")
result = pipeline.run("path/to/your_document.md")
print(f"Ingested: {result}")
```

```bash
python ingest_demo.py
```

The pipeline will:
- Parse the document via `kg-doc-parser`
- Store parsed artifacts in the **conversation graph** (`conv:fg`)
- Generate candidate links in the **background lane** (`conv:bg`)
- Emit a `maintenance_job_request` event for the distillation worker

---

## Step 3 — Promote a candidate to the Knowledge Graph

Promotion moves a candidate entity from conversation-space into the durable **knowledge graph**:

```python
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline

pipeline = IngestPipeline(workspace_id="demo")

# List promotion candidates
candidates = pipeline.list_promotion_candidates()
for c in candidates:
    print(c.label, c.confidence)

# Promote one (or let auto-promotion kick in for confidence >= threshold)
pipeline.promote(entity_id=candidates[0].id)
```

---

## Step 4 — Run the background workers

Two daemons run independently. Open two terminals:

```bash
# Terminal 1 — maintenance daemon (distillation + wisdom)
llm-wiki daemon maintenance --workspace demo --interval 10

# Terminal 2 — projection daemon (Obsidian sync)
llm-wiki daemon projection --workspace demo --vault ~/obsidian/wiki --interval 5
```

Both daemons respond to `Ctrl-C` (SIGINT) with a clean shutdown.

To embed in your own process instead:

```python
from kogwistar_llm_wiki.daemon import MaintenanceDaemon, ProjectionDaemon
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
import threading

pipeline = IngestPipeline(workspace_id="demo")
engines = pipeline.engines

m_daemon = MaintenanceDaemon(engines, workspace_id="demo", poll_interval=10.0)
p_daemon = ProjectionDaemon(engines, workspace_id="demo",
                             vault_root="~/obsidian/wiki", poll_interval=5.0)

# Run in background threads
threading.Thread(target=m_daemon.run, daemon=True).start()
threading.Thread(target=p_daemon.run, daemon=True).start()

# ... your app logic ...

m_daemon.stop()
p_daemon.stop()
```

---

## Step 5 — Inspect the Obsidian vault

Open the vault directory in Obsidian. You should see:
- One note per promoted entity
- Wikilinks between related entities
- Canvas files showing entity clusters

The vault is rebuilt incrementally on each projection cycle — only changed nodes trigger file writes.

---

## Step 6 — Run the test suite

```bash
pytest tests/unit/          # fast unit tests, no external services needed
pytest -m integration       # Obsidian vault + other on-disk integration checks
pytest -m manual            # opt-in smoke cases that need local services like Ollama
```

After a successful run, the next checks are:
- maintenance jobs in the durable meta-store should be `DONE`
- projection jobs in the durable meta-store should be `DONE`
- the projection manifest row should be `ready`
- the Obsidian vault should contain the expected `.md` files

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: kogwistar` | Run `bash scripts/bootstrap-dev.sh` to install local checkout |
| `ImportError: kogwistar-obsidian-sink` | Same — bootstrap installs it editable |
| Local edits to `kogwistar/` not reflected | Confirm `pip show kogwistar` points to local path |
| Projection daemon exits immediately | Vault directory may not exist — create it first |
| `PydanticDeprecatedSince20: min_items` warnings | These come from the installed `kogwistar` package; safe to ignore |
