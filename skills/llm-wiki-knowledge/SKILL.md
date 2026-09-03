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
5. Inspect nodes, edges, hyperedges, grounding, source watermark, and citations.
6. Validate a proposed graph edit against the current lens and evidence.
7. Apply a change only after explicit confirmation.

`no_change` is a successful result. Never invent a node, edge, evidence ID,
source span, revision, or confidence value. If evidence is insufficient, say
so and ask for more grounded input.

## Safety

The graph, workflow history, usage events, and checkpoints are authoritative.
Answers and OTel traces are observations. Proposal validation does not mutate
the graph. Confirmation is the mutation boundary and must use the exact
reviewed interaction and proposal.

When available, prefer the structured `llm_wiki` response extension over plain
answer text because it carries lens scope and grounding metadata.
