# CLI Reference - `llm-wiki`

All commands are available after installing the package:

```bash
pip install -e ".[dev]"
```

## Top-Level

```text
llm-wiki [--data-dir <path>] [--split-derived-knowledge] <command>
```

| Option | Default | Description |
|---|---|---|
| `--data-dir` | `.` | Path to persistent data directory |
| `--split-derived-knowledge` | off | Host `derived_knowledge` on a separate engine |

Hosting tradeoff for `derived_knowledge`:

- Default same-engine mode keeps raw KG and `derived_knowledge` on the same backend, but in different namespaces (`ws:{id}:kg` versus `ws:{id}:kg:derived`).
- Split-engine mode isolates storage and indexing cost, but cross-surface search must query two engines deliberately.
- The semantic contract is the same in both layouts: `derived_knowledge` never lives in the raw KG namespace.

## `llm-wiki daemon`

Both daemons poll on a configurable interval, treat `Ctrl-C` / `SIGTERM` as
graceful stop requests, and call the core `engine.recovery.recover_startup(...)`
coordinator before polling after restart.

Core startup recovery:

1. Safely repairs missing lane-message projection rows from graph/entity-event truth.
2. Reports durable queues, lane rows, checkpoints, run history, dead letters, and daemon health.
3. Leaves durable job claims and lane claims to existing lease expiry semantics.
4. Keeps workflow checkpoint auto-resume disabled unless an explicit restartable policy and resume hook are supplied.

### `daemon projection`

Drain the Obsidian projection queue for a workspace and keep the vault in sync.

```bash
llm-wiki daemon projection \
  --workspace <workspace-id> \
  --vault     <path-to-obsidian-vault> \
  [--interval <seconds>]
```

Startup recovery passes these app-specific surfaces into core:

- projection manifest state
- vault materialization state
- projection daemon health

Each poll cycle:

1. Claim durable projection jobs from the queue facade.
2. Call `ProjectionManager.sync_obsidian_vault()` through the Obsidian sink.
3. Emit append-only projection status events.
4. Mark jobs done or retry/fail through `engine.jobs`.
5. Sleep `--interval` seconds before the next poll.

### `daemon maintenance`

Drain the maintenance job queue and run the synthesis plus execution-wisdom path.

```bash
llm-wiki daemon maintenance \
  --workspace <workspace-id> \
  [--interval <seconds>]
```

Startup recovery passes maintenance daemon health into core and uses the core
report for queue/lane/checkpoint/run/dead-letter visibility.

Each poll cycle:

1. Claim durable maintenance jobs from the queue facade.
2. Process `distill` and `execution_wisdom` requests.
3. Emit a reply lane message.
4. Mark the durable job `DONE` or retry/fail it through `engine.jobs`.
5. Sleep `--interval` seconds before the next poll.

Current semantics:

- `distill` writes replacement `derived_knowledge` nodes in `ws:{id}:kg:derived`.
- `execution_wisdom` scans failure traces and emits `execution_wisdom` nodes.
- Interrupted work is recovered by core projection repair plus lease redelivery.
- Delivery remains at-least-once; duplicate execution should converge through deterministic IDs, completion checks, and versioned replacement.

## Programmatic API Cheatsheet

```python
from kogwistar_llm_wiki.daemon import MaintenanceDaemon, ProjectionDaemon
from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline

pipeline = IngestPipeline(workspace_id="demo")
engines = pipeline.engines

pipeline.run("doc.md")

m = MaintenanceDaemon(engines, "demo", poll_interval=10.0)
p = ProjectionDaemon(engines, "demo", vault_root="/tmp/vault", poll_interval=5.0)

report = m.recover_startup_state()
print(report.repaired_count, len(report.dead_letters))
```

## Running `python -m kogwistar_llm_wiki`

Equivalent to the `llm-wiki` script:

```bash
python -m kogwistar_llm_wiki daemon projection --workspace demo --vault /tmp/vault
python -m kogwistar_llm_wiki --help
```

## Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `KOGWISTAR_DATA_DIR` | CLI | Default persistent data directory when supplied by the caller |
| `PYTHONPATH` | dev | Ensure `src/` is importable without install |

## Test Commands

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/ -q
.venv\Scripts\python.exe -m pytest tests/unit/test_temporary_namespace.py -q
.venv\Scripts\python.exe -m pytest tests/unit/test_projection_consistency.py -q
```
