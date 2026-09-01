# Current Repo Deep Review

This document records the current status of the deep-review findings across the parser workflow, maintenance operation mode, and cross-repo coupling to `kogwistar`.

The original review identified several contract and runtime risks. Most of the high- and medium-severity items covered here have now been addressed in code. The remaining open item is primarily repo hygiene and reviewability rather than a known behavioral regression.

## Reviewed Surface

I reviewed the boundary-first parser path in `kg-doc-parser`, the surrounding workflow/runtime wiring, the maintenance operation-mode path in the host app, and the local `kogwistar` primitive that now supplies fuzzy offset repair.

The main evidence surface was:
- [`layerwise_llm.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/layerwise_llm.py>)
- [`handlers.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/handlers.py>)
- [`parser_core.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/parser_core.py>)
- [`models.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/models.py>)
- [`test_workflow_ingest_layerwise_llm.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/tests/test_workflow_ingest_layerwise_llm.py>)
- [`fuzzy_offsets.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kogwistar/kogwistar/fuzzy_offsets.py>)
- [`ingest_pipeline.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/ingest_pipeline.py>)
- [`worker.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/worker.py>)
- [`maintenance_policy.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/maintenance_policy.py>)
- [`maintenance_strategies.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/maintenance_strategies.py>)

## Findings

### Resolved High: Boundary anchors are now required by the schema and by validation

The boundary-cutpoint contract is now aligned instead of being split between permissive model defaults and stricter runtime validation.

Evidence:
- [`models.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/models.py>) now declares `text_before_cut`, `text_after_cut`, and `cut_reason` as required fields on `BoundaryCutpoint`.
- [`layerwise_llm.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/layerwise_llm.py>) still validates those anchors before accepting a proposal.
- The deterministic legal-boundary helper in the same file now fills those required fields for internally generated cutpoints.

Why it mattered:
- Schema-valid-but-runtime-invalid cutpoints were creating avoidable retry and fallback churn.

Remediation status:
- Fixed.

### Resolved High: Boundary mode now preserves valid atomic or no-split decisions

Boundary mode no longer converts a legitimate `satisfied=True` and empty-cutpoint response into fallback children.

Evidence:
- [`layerwise_llm.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/layerwise_llm.py>) now returns an explicit atomic boundary result with `boundary_atomic_decision=True` instead of treating the case as proposal failure.
- [`test_workflow_ingest_layerwise_llm.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/tests/test_workflow_ingest_layerwise_llm.py>) now asserts the atomic no-split result directly.

Why it mattered:
- A valid “already atomic” layer should not be rewritten into synthetic fallback output.

Remediation status:
- Fixed.

### Resolved High: Maintenance jobs now distinguish maintenance kinds for the same source document

The maintenance queue identity now keeps different `maintenance_kind` phases from collapsing into one durable job row.

Evidence:
- [`ingest_pipeline.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/ingest_pipeline.py>) now enqueues and probes maintenance jobs with `job_kind=f"maintenance_job:{maintenance_kind}"`.
- [`test_ingest_pipeline_ingest.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/tests/unit/test_ingest_pipeline_ingest.py>) now verifies that two maintenance kinds for the same `source_document_id` produce two queue entries.

Why it mattered:
- `maintenance_first` seeding work and later expansion or distillation work need to coexist for the same document.

Remediation status:
- Fixed.

### Resolved Medium: `kg-doc-parser` dependency now matches the required `kogwistar` symbol surface

The parser dependency declaration now reflects the version that actually provides `kogwistar.fuzzy_offsets`.

Evidence:
- [`pyproject.toml`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/pyproject.toml>) now pins `kogwistar = "^0.2.4"`.
- [`layerwise_llm.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/layerwise_llm.py>) still imports the fuzzy-offset helpers from that package surface.

Why it mattered:
- Older installs satisfying `^0.2.0` could import a parser version that expected symbols they did not actually contain.

Remediation status:
- Fixed.

### Resolved Medium: Runtime maintenance jobs now preserve non-success statuses

The maintenance worker no longer flattens every non-exception runtime return into a completed job and completed reply.

Evidence:
- [`worker.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/src/kogwistar_llm_wiki/worker.py>) now branches on runtime status, only marks done for successful terminal states, and preserves `suspended` at the lane-message level.
- [`test_worker_runtime_orchestration.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/tests/unit/test_worker_runtime_orchestration.py>) now verifies that a suspended maintenance run is not marked done and emits a suspended reply.

Why it mattered:
- Operators need truthful status surfaces for resume and retry handling.

Remediation status:
- Fixed.

### Resolved Medium: `semantic_tree` mutation is now inside the workflow write transaction

The initial parse-session step now writes `semantic_tree` under the same `state_write` block as the other state updates.

Evidence:
- [`handlers.py`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/kg_doc_parser/workflow_ingest/handlers.py>) now assigns `st["semantic_tree"] = root.model_dump()` inside the `with ctx.state_write as st:` block.

Why it mattered:
- The previous version relied on mutable state escape behavior that was convenient but brittle if the runtime transaction model tightened later.

Remediation status:
- Fixed.

### Low: Line-ending churn is only partially addressed

The repo now has `.gitattributes` guardrails, but the current worktree still contains broad pre-existing CRLF noise outside the files touched for this fix pass.

Evidence:
- [`.gitattributes`](/mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/.gitattributes) and [`kg-doc-parser/.gitattributes`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/.gitattributes>) were added to establish stable line-ending policy.
- `git diff --ignore-cr-at-eol --stat` still shows that the broader workspace and nested parser repo contain many unrelated modified files beyond the semantic fix set.

Why it matters:
- The new policy reduces future churn, but existing dirty files can still make review harder until they are normalized or split into separate commits.

Recommended direction:
- Keep the new `.gitattributes` policy.
- Normalize or isolate existing formatting-only file changes in a dedicated cleanup pass instead of mixing them into behavior work.

Remediation status:
- Partially fixed.

## Open Questions / Assumptions

- I assumed the root `doc/` is still the right home because this review spans parser, app wiring, and substrate coupling.
- I assumed the intended audience remains maintainers triaging near-term fixes rather than external users.
- I assumed `kg-doc-parser/.gitattributes` should be committed only if that nested repo is actively maintained in this workspace rather than treated as a read-only upstream mirror.

## Suggested Next Actions

1. Keep the current behavioral fixes and their regression tests together when the work is committed.
2. Decide whether the nested [`kg-doc-parser/.gitattributes`](</mnt/c/Users/chanh/Documents/kogwistar-llm-wiki/kg-doc-parser/.gitattributes>) should remain as repo-local policy or be dropped if that repo is meant to stay upstream-clean.
3. Do a separate formatting-only cleanup pass if the broader CRLF churn is still hurting review velocity.

## Verification Notes

- `python -m py_compile` passed on the touched production files and new regression tests.
- `python -m pyflakes` passed on the same touched files.
- Focused and broadened pytest verification passed through the project Python 3.13 Windows venv using:
  `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider kg-doc-parser/tests/test_workflow_ingest_layerwise_llm.py tests/unit/test_ingest_pipeline_ingest.py tests/unit/test_worker_runtime_orchestration.py`
- The broadened touched-file test run completed at `33 passed`.
