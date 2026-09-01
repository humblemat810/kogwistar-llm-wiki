---
name: llm-wiki-knowledge
description: Use llm-wiki for grounded knowledge management: query a scoped graph, inspect provenance and history, and propose only explicitly reviewed changes.
---

# llm-wiki Knowledge Management

Use the llm-wiki gateway or MCP tools to manage and query a scoped knowledge
graph. Always identify the workspace and preserve the session when continuing
a conversation.

## Workflow

1. Search or ask for a bounded knowledge lens.
2. Inspect nodes, edges, hyperedges, grounding, source watermark, and citations.
3. Query interaction history when the user asks what happened previously.
4. Validate a proposed graph edit against the current lens and evidence.
5. Apply a change only after explicit confirmation.

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
