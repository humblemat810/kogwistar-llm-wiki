# Cross-Link Parse Comparison

This fixture compares the background cross-link path before and after a real
parse-and-persist phase. It is intentionally deterministic and does not use
production data or require a live model provider.

## Cases

The integration test uses `cuda_relationship.md`, whose two sentences become
two parsed child pieces:

```text
NVIDIA builds CUDA.
The training service uses CUDA kernels.
```

Case A skips parsing and supplies no parsed evidence. The proposal lifecycle
records `no_candidate` and does not invoke a provider proposal.

Case B runs `IngestPipeline` with a deterministic parser, persists the source
graph, then supplies the two child pieces as exact evidence from the same
immutable source revision. The provider returns one known `uses` proposal;
the normal group validation and critic-routing path accepts it for the test
harness.

The test records the comparison audit fields explicitly: parser stages,
evidence IDs passed to the provider, provider and critic call counts, proposal
group and review status, validation failures, queued mutation IDs, configured
call/token/step budgets, workflow stages, and the workspace namespace of every
provider-visible node. The unparsed case asserts zero provider, critic, and
mutation calls; the parsed case asserts one bounded call through each stage.

The additional `cuda_platform.md` and `cuda_service.md` fixtures provide the
minimal two-document variant for future provider runs. They make the intended
cross-document relationship explicit without relying on a large corpus.

## Result

The comparison proves that the earlier no-candidate result cannot be
interpreted as a parser failure: without parsed pieces there is no eligible
evidence, while persisted pieces make a same-document proposal possible.
The fake provider is used for repeatable CI evidence. A live Bonsai run is
optional and must remain bounded; its output must not be treated as a test
oracle or allowed to mutate production data.

## Optional Neighbor Context

Cross-link proposal and critic prompts can request bounded, one-hop context
around the endpoint evidence. The feature is opt-in; omitting the setting
preserves the endpoint-only prompt and does not perform extra graph reads.

Use the maintenance job payload form below:

```json
{
  "crosslink_context_budget": {
    "max_nodes": 16,
    "max_edges": 24,
    "max_tokens": 2400,
    "max_characters": 12000
  }
}
```

All four limits are optional and must be non-negative. `max_chars` is accepted
as an alias for `max_characters`; the same fields are also accepted as
top-level compatibility aliases (`crosslink_context_max_nodes`,
`crosslink_context_max_edges`, `crosslink_context_max_tokens`, and
`crosslink_context_max_characters`). When any context setting is present but a
node or edge count is omitted, conservative defaults of 16 nodes and 24 edges
apply. A zero value means unbounded by that dimension, subject to the worker's
hard graph scan ceiling.

The worker filters workspace and ACL scope before packing records. It then
uses Kogwistar's `ContextItem` and `ConversationContextBuilder` semantics for
stable graph-derived ordering and token-budget accounting, followed by the
character limit. The provider receives node summaries and edge topology as
descriptive context only. It cannot cite neighbor context as mutation
evidence; endpoint evidence still requires exact authoritative source spans,
and all existing validation and critic checks remain mandatory.

The prompt envelope reports `omitted_nodes`, `omitted_edges`, estimated token
usage, and character usage. Token counts are conservative estimates rather
than a claim about a provider's exact tokenizer. The worker emits a
`maintenance_crosslink_context_built` trace for enabled requests, so operators
can distinguish a bounded context run from the legacy endpoint-only path.
