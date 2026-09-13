---
name: llm-wiki-knowledge
description: Use llm-wiki for grounded knowledge management: query a scoped graph, inspect provenance and history, manage canonical sources, and propose only explicitly reviewed changes.
---

# llm-wiki Knowledge Management

Use the llm-wiki gateway or MCP tools to manage and query a scoped knowledge
graph. Always identify the workspace and preserve the session when continuing
a conversation.

## Workflow

1. Use `query` for a grounded answer or `search` for bounded retrieval.
2. Use `ingest`, `source`, and `reingest` for canonical source lifecycle.
3. Use `maintain` for directed work and `status` for health and progress.
4. Use `hypergraph_search` for bounded structural inspection and `history` for prior context.
5. Before relevant project planning, debugging, design, or continuation work,
   call `memory_recall`; skip it for unrelated trivial tasks.
6. Inspect nodes, edges, hyperedges, grounding, source watermark, and citations.
7. Capture only durable project facts with `memory_capture` when the operator has
   enabled it. Every record needs bounded evidence and must label `verified` or
   `inferred` confidence.
8. Use `memory_review` when current evidence conflicts with prior memory; treat
   superseded or inferred records as context, not unquestionable truth.
9. Validate a proposed graph edit against the current lens and evidence.
10. Apply a canonical change only after explicit confirmation.

`no_change` is a successful result. Never invent a node, edge, evidence ID,
source span, revision, or confidence value. If evidence is insufficient, say
so and ask for more grounded input. Do not capture secrets, tokens, private
keys, raw transcripts, host filesystem paths, or unbounded tool output.

## Safety

The graph, workflow history, usage events, and checkpoints are authoritative.
Answers and OTel traces are observations. Proposal validation does not mutate
the graph. Confirmation is the mutation boundary and must use the exact
reviewed interaction and proposal.

When available, prefer the structured `llm_wiki` response extension over plain
answer text because it carries lens scope and grounding metadata.

For a trusted local personal deployment, `LLM_WIKI_AUTH_MODE=disabled` is the
explicit no-identity mode. Do not treat it as a named default user or use it
for a shared service. Always provide the intended workspace even in this mode;
authentication and workspace scoping are separate concerns.
