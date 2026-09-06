---
name: llm-wiki-knowledge
description: Manage and query a Kogwistar llm-wiki graph through grounded lenses, provenance, history, and explicit proposals.
---

# llm-wiki Knowledge Management

Use the llm-wiki MCP server for graph questions. Always provide the configured
workspace ID and preserve the current session ID when continuing an inquiry.

## Required sequence

1. Search or ask for a bounded lens.
2. Read returned nodes, edges, grounding, source watermark, and citations.
3. Use history when the user asks what happened earlier.
4. Validate a proposed edit before presenting it as actionable.
5. Apply a change only after explicit confirmation.

`no_change` is a successful outcome. Never invent a node, edge, evidence ID,
source span, revision, or confidence value. If grounding is insufficient,
state that clearly and ask for more evidence.

## Safety boundary

The graph and workflow history are authoritative. MCP output and OTel traces
are observations, not graph truth. Proposal validation does not mutate the
graph. Confirmation is the only mutation boundary and must use the exact
reviewed interaction and proposal.
