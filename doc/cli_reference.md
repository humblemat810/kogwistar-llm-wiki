# CLI Reference — `llm-wiki`

All commands are available after installing the package:

```bash
pip install -e ".[dev]"
# or after bootstrap:
bash scripts/bootstrap-dev.sh
```

---

## Top-level

```
llm-wiki [--data-dir <path>] <command>
```

| Option | Default | Description |
|---|---|---|
| `--data-dir` | `.` | Path to persistent data directory (SQLite meta-store, Chroma data) |

---

## `llm-wiki daemon`

Run a long-lived background worker. Both daemons poll on a configurable interval and shut down cleanly on `Ctrl-C` / `SIGTERM`.

### `daemon projection`

Drain the Obsidian projection queue for a workspace and keep the vault in sync.

```bash
llm-wiki daemon projection \
  --workspace <workspace-id> \
  --vault     <path-to-obsidian-vault> \
  [--interval <seconds>]              # default: 5.0
```

**What it does on each poll cycle:**
1. Read the last projected sequence number from the meta-store
2. Query the graph for the next `projection_request` node (`seq = last + 1`)
3. Call `ProjectionManager.sync_obsidian_vault()` → `kogwistar-obsidian-sink`
4. Emit an append-only `projection_status_event` node (processing → completed/failed)
5. Advance the sequence counter in the meta-store
6. Stop when the queue is empty; sleep `--interval` seconds before next poll

**Example:**

```bash
llm-wiki daemon projection --workspace my-wiki --vault ~/Documents/ObsidianWiki
```

---

### `daemon maintenance`

Drain the maintenance job queue and run the synthesis + execution-wisdom pipeline.

```bash
llm-wiki daemon maintenance \
  --workspace <workspace-id> \
  [--interval <seconds>]              # default: 10.0
```

**What it does on each poll cycle:**
1. Scan `conv:bg` for `maintenance_job_request` nodes
2. Skip any request that already has a `workflow_completed` trace
3. For each pending request, run `maintenance.distillation.v1` workflow:
   - `distill` — aggregate promoted knowledge → versioned wisdom nodes
   - `distill_from_history` *(optional step)* — scan workflow failure patterns → execution_wisdom nodes
   - `check_done` → loop or finish
4. Sleep `--interval` seconds before next poll

Current semantics:
- `distill` produces versioned `derived_knowledge` nodes from promoted KG knowledge in `ws:{id}:kg:derived`
- execution-history analysis runs immediately after the workflow finishes and emits `execution_wisdom` nodes for repeated failure patterns

**Example:**

```bash
llm-wiki daemon maintenance --workspace my-wiki --interval 30
```

---

## Running `python -m kogwistar_llm_wiki`

Equivalent to the `llm-wiki` script:

```bash
python -m kogwistar_llm_wiki daemon projection --workspace demo --vault /tmp/vault
python -m kogwistar_llm_wiki --help
```

---

## Programmatic API cheatsheet

```python
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
from kogwistar_llm_wiki.daemon import MaintenanceDaemon, ProjectionDaemon

# Build engines for a workspace
pipeline = IngestPipeline(workspace_id="demo")
engines  = pipeline.engines

# Ingest
pipeline.run("doc.md")

# List and promote candidates
candidates = pipeline.list_promotion_candidates()
pipeline.promote(entity_id=candidates[0].id)

# Projection snapshot (no vault write)
from kogwistar_llm_wiki.projection import ProjectionManager
snap = ProjectionManager(engines).build_projection_snapshot("demo")
print(len(snap.entities), "KG-visible entities")

# Run workers in-process
m = MaintenanceDaemon(engines, "demo", poll_interval=10.0)
p = ProjectionDaemon(engines, "demo", vault_root="/tmp/vault", poll_interval=5.0)
import threading
threading.Thread(target=m.run, daemon=True).start()
threading.Thread(target=p.run, daemon=True).start()
# ... later:
m.stop(); p.stop()
```

---

## Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `KOGWISTAR_DATA_DIR` | future | Override default data directory (not yet enforced) |
| `PYTHONPATH` | dev | Ensure `src/` is importable without install |

---

## Test commands cheatsheet

```bash
# All fast unit tests
pytest tests/unit/ -q

# Verbose with short tracebacks
pytest tests/unit/ -v --tb=short

# Only namespace proxy tests
pytest tests/unit/test_temporary_namespace.py -v

# Only projection tests
pytest tests/unit/test_projection_consistency.py -v

# Opt-in integration tests (require real vault / Chroma)
pytest -m integration

# Opt-in manual tests
pytest -m manual
```
