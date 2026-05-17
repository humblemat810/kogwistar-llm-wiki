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

## Goals

- [ ] Preserve `parse_page_index_document(...)` as the page-index public API.
- [ ] Keep `mode="heuristic"` deterministic, fast, and non-LLM.
- [ ] Make `mode="ollama"` use hybrid flat-assignment parsing instead of
  recursive tree generation.
- [ ] Ensure validation failure never silently appears as a "100% coverage"
  parse.
- [ ] Keep long-run parser subprocess diagnostics and runtime JSONL traces
  working.
- [ ] Keep page-index parsing selectable from the long-run harness.

## Non-Goals

- [ ] Do not use the legacy non-workflow iterative parser for new long-run
  workflows.
- [ ] Do not replace workflow-native layered parsing; page-index remains a
  separate diagnostic and comparative parser lane.
- [ ] Do not require Ollama for deterministic unit tests.
- [ ] Do not make the LLM responsible for generating final recursive
  `child_nodes`.

## Slice 1: Page-Index Core Primitives

**Goal:** introduce the data model needed for deterministic extraction,
assignment, validation, and assembly without changing behavior yet.

- [ ] Add `CandidateBlock` with:
  - [ ] stable `block_id`
  - [ ] page number and reading order
  - [ ] text span offsets
  - [ ] line range
  - [ ] indent
  - [ ] kind hint
  - [ ] confidence
- [ ] Add `BlockAssignment` with:
  - [ ] `block_id`
  - [ ] `parent_id`
  - [ ] `node_type`
  - [ ] `title`
- [ ] Add `PageIndexValidationResult` with:
  - [ ] `valid`
  - [ ] `errors`
  - [ ] `warnings`
  - [ ] `fallback_reason`
- [ ] Export the new primitives from `kg_doc_parser.workflow_ingest` if they
  are intended as supported parser diagnostics.
- [ ] Add narrow unit tests proving the primitives serialize and validate
  predictably.

## Slice 2: Deterministic Candidate Extraction

**Goal:** extract stable candidate blocks before any LLM call.

- [ ] Implement markdown-like candidate extraction:
  - [ ] ATX headings
  - [ ] Setext headings
  - [ ] bullet lists
  - [ ] ordered lists
  - [ ] paragraph blocks
- [ ] Implement quasi-markdown/plain-text candidate extraction:
  - [ ] numbered headings
  - [ ] legal subclauses such as `(a)` and `(i)`
  - [ ] short all-caps headings
  - [ ] title-case standalone headings
  - [ ] paragraph merging
- [ ] Add deterministic confidence scoring:
  - [ ] promote headings surrounded by blank lines
  - [ ] promote numbered or clause-like lines
  - [ ] demote full-sentence heading candidates
  - [ ] demote numeric-heavy or table-like lines
- [ ] Preserve exact source text for every candidate excerpt.
- [ ] Add tests for:
  - [ ] clean markdown headings and lists
  - [ ] quasi-markdown numbered findings
  - [ ] all-caps heading versus all-caps emphasis
  - [ ] no-heading flat pages
  - [ ] dense subclauses like `(a)`, `(i)`, and `1.2.3`

## Slice 3: Deterministic Assignment And Assembly

**Goal:** make a valid deterministic page-index tree without the LLM.

- [ ] Implement deterministic `CandidateBlock -> BlockAssignment` fallback.
- [ ] Implement deterministic assembler from flat assignments to
  `PageIndexBlockSpec`.
- [ ] Keep reading order stable in the assembled tree.
- [ ] Ground every final excerpt in the candidate's exact source text.
- [ ] Preserve current `mode="heuristic"` behavior where possible, but route it
  through the new candidate/assignment/assembly path.
- [ ] Add assembler tests for:
  - [ ] stable reading order
  - [ ] deterministic parent repair
  - [ ] exact excerpt grounding
  - [ ] fallback tree still produces a valid KGE payload

## Slice 4: Assignment Validation

**Goal:** reject superficially high-coverage but semantically wrong parses.

- [ ] Validate every candidate block is assigned exactly once.
- [ ] Reject duplicate block assignments.
- [ ] Reject missing block assignments.
- [ ] Reject unknown block IDs.
- [ ] Reject `parent_id` values that reference later blocks.
- [ ] Reject cycles.
- [ ] Reject repeated sibling excerpts that indicate whole-page duplication.
- [ ] Reject duplicated whole-page excerpts masquerading as child blocks.
- [ ] Require `SECTION` and `SUBSECTION` to have heading-like evidence or
  children.
- [ ] Require `TERM` to have short/list/label-like evidence.
- [ ] Preserve reading order.
- [ ] Add validator tests for every failure class above.
- [ ] Ensure failed validation returns a clear fallback reason.

## Slice 5: Ollama Flat-Assignment Lane

**Goal:** shrink the local model task to flat block assignment.

- [ ] Replace recursive `PageIndexBlockSpec` structured output for
  `mode="ollama"` with a flat `BlockAssignment` list.
- [ ] Send the model only candidate block IDs, compact excerpts, kind hints,
  and confidence scores.
- [ ] Move critical parser rules into user content, not only system messages,
  to support Gemma-style prompt templates.
- [ ] Keep model instructions explicit:
  - [ ] do not invent block IDs
  - [ ] assign every block exactly once
  - [ ] use only earlier blocks as parents
  - [ ] do not use whole-page excerpts
  - [ ] preserve reading order
- [ ] Validate the model assignment before assembly.
- [ ] Fall back to deterministic assignment on malformed JSON, invalid schema,
  missing blocks, shallow output, or validation errors.
- [ ] Add fake-model tests for:
  - [ ] valid flat assignments
  - [ ] malformed JSON
  - [ ] missing block IDs
  - [ ] forward parent references
  - [ ] too-shallow output
  - [ ] deterministic fallback after validation failure

## Slice 6: Diagnostics And Long-Run Visibility

**Goal:** make parser quality visible in normal artifacts.

- [ ] Extend `PageIndexParseResult.coverage` or compatible metadata with:
  - [ ] assignment mode
  - [ ] fallback reason
  - [ ] candidate count
  - [ ] assignment count
  - [ ] validation errors
  - [ ] validation warnings
- [ ] Update `longrun_parser_worker.py` diagnostics to include:
  - [ ] parser lane
  - [ ] assignment mode
  - [ ] fallback reason
  - [ ] candidate count
  - [ ] block count
- [ ] Keep parser trace breadcrumbs for subprocess-only diagnosis.
- [ ] Keep runtime JSONL sink for workflow-level progress.
- [ ] Add a long-run parser child regression that asserts diagnostics include
  assignment/fallback metadata.

## Slice 7: Optional Excerpt Refinement

**Goal:** allow small model assistance only after structure is already valid.

- [ ] Add an optional excerpt-refinement micro-pass after assignment validation.
- [ ] Require refined excerpts to remain exact substrings of source text.
- [ ] Reject refined excerpts that duplicate whole pages or sibling excerpts.
- [ ] Keep excerpt refinement disabled by default until tests prove stability.
- [ ] Add tests for accepted and rejected refinements.

## Slice 8: Compatibility Cleanup

**Goal:** remove or quarantine the old recursive LLM tree path after the hybrid
lane is green.

- [ ] Keep the recursive LLM tree path only as temporary compatibility if
  needed during migration.
- [ ] Remove recursive tree generation once hybrid tests and manual probes are
  reliable.
- [ ] Update docs to state `mode="ollama"` means candidate extraction plus flat
  assignment.
- [ ] Keep `gemma4:e2b` selectable only for comparison, not as the recommended
  default.
- [ ] Prefer local parser models in this order for manual page-index runs:
  - [ ] `qwen3:4b-instruct-2507-q8_0`
  - [ ] `qwen3:4b`
  - [ ] `gemma3:4b-it-qat`

## Acceptance Criteria

- [ ] A bad model response cannot pass solely because it repeats the whole page
  as every child excerpt.
- [ ] `mode="heuristic"` remains deterministic and does not require Ollama.
- [ ] `mode="ollama"` can succeed with valid flat assignments from a fake model.
- [ ] `mode="ollama"` falls back deterministically on invalid model output.
- [ ] Existing page-index heuristic tests stay green.
- [ ] Existing long-run parser-lane tests stay green.
- [ ] Long-run page-index diagnostics show assignment mode and fallback reason.
- [ ] Manual Ollama page-index runs no longer depend on recursive tree quality
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
