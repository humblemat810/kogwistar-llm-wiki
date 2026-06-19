# Page-Index Parser Refactor Checklist

## Summary

Refactor page-index parsing from "ask the LLM to generate a recursive tree
from the whole page" into a hybrid parser:

1. deterministically extract candidate blocks
2. optionally ask a local model for flat block assignments
3. validate the assignments
4. deterministically assemble `PageIndexBlockSpec`
5. fall back to deterministic parsing when model output is weak

The public `parse_page_index_document(...)` entrypoint should stay compatible
for current callers, including the long-run harness.

## Status

- `Slices 1-6` are implemented in the current codebase.
- `Slice 8` compatibility cleanup is mostly implemented:
  the duplicate helper drift was removed, the hybrid path is now the active
  `mode="ollama"` behavior, and the page-index-facing docs now point at the
  Qwen-first manual guidance instead of the old Gemma-first examples.
- `Slice 7` excerpt refinement is implemented as an optional default-off
  micro-pass with validation and regression coverage.
- Verification currently includes focused parser tests plus long-run parser-lane
  regressions.
- During verification, we also fixed an adjacent runtime/read bug so empty
  node or edge reads return `[]` instead of raising `"Missing Embeddings"` when
  a collection is empty.

## Goals

- [x] Preserve `parse_page_index_document(...)` as the page-index public API.
- [x] Keep `mode="heuristic"` deterministic, fast, and non-LLM.
- [x] Make `mode="ollama"` use hybrid flat-assignment parsing instead of
  recursive tree generation.
- [x] Ensure validation failure never silently appears as a "100% coverage"
  parse.
- [x] Keep long-run parser subprocess diagnostics and runtime JSONL traces
  working.
- [x] Keep page-index parsing selectable from the long-run harness.

## Non-Goals

- [x] Do not use the legacy non-workflow iterative parser for new long-run
  workflows.
- [x] Do not replace workflow-native layered parsing; page-index remains a
  separate diagnostic and comparative parser lane.
- [x] Do not require Ollama for deterministic unit tests.
- [x] Do not make the LLM responsible for generating final recursive
  `child_nodes`.

## Slice 1: Page-Index Core Primitives

**Goal:** introduce the data model needed for deterministic extraction,
assignment, validation, and assembly without changing behavior yet.

- [x] Add `CandidateBlock` with:
  stable `block_id`, page number, reading order, source spans, line range,
  indent, kind hint, confidence, text, node type hint, title hint, and
  optional heading level.
- [x] Add `BlockAssignment` with:
  `block_id`, `parent_id`, `node_type`, and `title`.
- [x] Add `PageIndexValidationResult` with:
  `valid`, `errors`, `warnings`, and `fallback_reason`.
- [x] Export the new primitives from `kg_doc_parser.workflow_ingest` if they
  are intended as supported parser diagnostics.
- [x] Add narrow unit tests proving the primitives serialize and validate
  predictably.

## Slice 2: Deterministic Candidate Extraction

**Goal:** extract stable candidate blocks before any LLM call.

- [x] Implement markdown-like candidate extraction:
  ATX headings, Setext headings, bullet lists, ordered lists, and paragraph
  blocks.
- [x] Implement quasi-markdown/plain-text candidate extraction:
  numbered headings, legal subclauses such as `(a)` and `(i)`, short
  all-caps headings, title-case standalone headings, and paragraph merging.
- [x] Add deterministic confidence scoring:
  current code now promotes blank-delimited heading candidates and demotes
  numeric-heavy or table-like lines away from heading confidence.
- [x] Preserve exact source text for every candidate excerpt.
- [x] Add tests for:
  clean markdown headings/lists, all-caps heading versus all-caps emphasis,
  no-heading flat pages, and dense `1.2.3` plus `(a)` / `(i)` clause-style
  candidates are now covered.

## Slice 3: Deterministic Assignment And Assembly

**Goal:** make a valid deterministic page-index tree without the LLM.

- [x] Implement deterministic `CandidateBlock -> BlockAssignment` fallback.
- [x] Implement deterministic assembler from flat assignments to
  `PageIndexBlockSpec`.
- [x] Keep reading order stable in the assembled tree.
- [x] Ground every final excerpt in the candidate's exact source text.
- [x] Preserve current `mode="heuristic"` behavior where possible, but route it
  through the new candidate/assignment/assembly path.
- [x] Add assembler tests for:
  stable reading order, valid KGE payload, and deterministic parent repair are
  now covered directly.

## Slice 4: Assignment Validation

**Goal:** reject superficially high-coverage but semantically wrong parses.

- [x] Validate every candidate block is assigned exactly once.
- [x] Reject duplicate block assignments.
- [x] Reject missing block assignments.
- [x] Reject unknown block IDs.
- [x] Reject `parent_id` values that reference later blocks.
- [x] Reject cycles.
- [x] Reject repeated sibling excerpts that indicate whole-page duplication.
- [x] Reject duplicated whole-page excerpts masquerading as child blocks.
- [x] Require `SECTION` and `SUBSECTION` to have heading-like evidence or
  children.
- [x] Require `TERM` to have short/list/label-like evidence.
- [x] Preserve reading order.
- [x] Add validator tests for every failure class above.
  Duplicate IDs, unknown IDs, heading evidence, repeated sibling excerpts,
  and whole-page duplication are now covered directly.
- [x] Ensure failed validation returns a clear fallback reason.

## Slice 5: Ollama Flat-Assignment Lane

**Goal:** shrink the local model task to flat block assignment.

- [x] Replace recursive `PageIndexBlockSpec` structured output for
  `mode="ollama"` with a flat `BlockAssignment` list.
- [x] Send the model only candidate block IDs, compact excerpts, kind hints,
  and confidence scores.
- [x] Move critical parser rules into user content, not only system messages,
  to support Gemma-style prompt templates.
- [x] Keep model instructions explicit:
  do not invent block IDs, assign every block exactly once, use only earlier
  blocks as parents, do not use whole-page excerpts, and preserve reading
  order.
- [x] Validate the model assignment before assembly.
- [x] Fall back to deterministic assignment on malformed JSON, invalid schema,
  missing blocks, shallow output, or validation errors.
- [x] Add fake-model tests for:
  valid flat assignments, malformed structured payloads, and too-shallow
  schema-valid output now all have direct regressions.

## Slice 6: Diagnostics And Long-Run Visibility

**Goal:** make parser quality visible in normal artifacts.

- [x] Extend `PageIndexParseResult` with first-class `diagnostics` metadata for
  assignment mode, fallback reason, candidate count, assignment count,
  validation errors, validation warnings, and per-page diagnostics.
- [x] Update `longrun_parser_worker.py` diagnostics to include:
  parser lane plus nested page-index diagnostics derived from the parser
  result.
- [x] Keep parser trace breadcrumbs for subprocess-only diagnosis.
- [x] Keep runtime JSONL sink for workflow-level progress.
- [x] Add a long-run parser child regression that asserts diagnostics include
  assignment/fallback metadata.

## Slice 7: Optional Excerpt Refinement

**Goal:** allow small model assistance only after structure is already valid.

- [x] Add an optional excerpt-refinement micro-pass after assignment validation.
- [x] Require refined excerpts to remain exact substrings of source text.
- [x] Reject refined excerpts that duplicate whole pages or sibling excerpts.
- [x] Keep excerpt refinement disabled by default until tests prove stability.
- [x] Add tests for accepted and rejected refinements.

## Slice 8: Compatibility Cleanup

**Goal:** remove or quarantine the old recursive LLM tree path after the hybrid
lane is green.

- [x] Keep the recursive LLM tree path only as temporary compatibility if
  needed during migration.
- [x] Remove recursive tree generation once hybrid tests and manual probes are
  reliable.
- [x] Update docs to state `mode="ollama"` means candidate extraction plus flat
  assignment.
- [x] Keep `gemma4:e2b` selectable only for comparison, not as the recommended
  default.
- [x] Prefer local parser models in this order for manual page-index runs:
  - [x] `qwen3:4b-instruct-2507-q8_0`
  - [x] `qwen3:4b`
  - [x] `gemma3:4b-it-qat`

## Acceptance Criteria

- [x] A bad model response cannot pass solely because it repeats the whole page
  as every child excerpt.
- [x] `mode="heuristic"` remains deterministic and does not require Ollama.
- [x] `mode="ollama"` can succeed with valid flat assignments from a fake model.
- [x] `mode="ollama"` falls back deterministically on invalid model output.
- [x] Existing page-index heuristic tests stay green.
- [x] Existing long-run parser-lane tests stay green.
- [x] Long-run page-index diagnostics show assignment mode and fallback reason.
- [x] Manual Ollama page-index runs no longer depend on recursive tree quality
  from a small local model.

## Focused Test Commands

```powershell
.\.venv\Scripts\python.exe -m pytest kg-doc-parser/tests/test_workflow_ingest_page_index_pipeline.py -q -p no:cacheprovider
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_longrun_workflow_ingestion.py -q -p no:cacheprovider -k "page_index or parser"
```

```powershell
.\.venv\Scripts\python.exe -m pytest kg-doc-parser/tests -q -p no:cacheprovider -k "page_index or workflow_ingest"
```

## Implementation Notes

- The LLM should classify and parent candidates; it should not create final
  recursive children.
- Validation owns correctness, not model confidence.
- Coverage should represent source grounding, not parser semantic quality by
  itself.
- Whole-page excerpt duplication must be treated as a parser-quality failure,
  even if source coverage appears high.
- The deterministic fallback is not a second-class path; it is the safety rail
  that makes local Ollama parsing usable.
