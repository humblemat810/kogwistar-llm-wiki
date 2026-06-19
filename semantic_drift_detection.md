Status: Updated after fix pass

The main semantic-drift issues identified in this extension have been fixed and covered by tests. The remaining differences are now mostly intentional design choices rather than violations of core Kogwistar semantics.

Resolved Drift

The source document identity is now stable in the same way as core Kogwistar. `source_document_id` now keys off `workspace_id + source_uri`, so changing the title no longer creates a brand-new logical document.

The extension now uses the durable `index_jobs` outbox with leases, retries, and reconciliation for maintenance and projection work. The old graph-node polling path is no longer the authoritative queue.

The projection state now lives in a named projection manifest row with `projected_ids` and materialization status, and `ProjectionManager` reads that manifest first.

The CLI bootstrap now constructs engines from the package factory instead of calling `IngestPipeline` with an invalid signature.

Verified by tests

- `tests/unit/test_ingest_pipeline_orchestration.py`
- `tests/unit/test_ingest_pipeline_projection.py`
- `tests/unit/test_projection_consistency.py`
- `tests/unit/test_worker_runtime_orchestration.py`
- `tests/unit/test_wisdom_distillation.py`
- `tests/integration/test_obsidian_vault_on_disk.py`

Likely Intentional, But Divergent

The extension's projection filter is still metadata-driven as a fallback when no manifest row exists. That is acceptable as a compatibility path, but it is not the same mechanism as core's named-projection/materialization contract.

Wisdom distillation is still versioned as brand-new node IDs on every run. That is append-only and traceable, but it remains a different lifecycle than stable-key projection rows.

The maintenance worker still produces graph traces for auditability, even though the durable job table is authoritative for scheduling.

Not A Drift

`kg` vs `knowledge` is not a semantic violation by itself. Core normalizes graph kinds, so using `kg_graph_type="knowledge"` in the extension is compatible with the core token set.

Current Fix Pass

The main drift items above were addressed in code and regression-tested.

Resolved during the fix:

- The maintenance job namespace is now separate from the graph audit namespace, so durable job rows and trace nodes cannot collide.
- The projection manifest namespace is shared between the worker and `ProjectionManager`, so the manifest row written by the worker is the same one the projection snapshot reads back.
