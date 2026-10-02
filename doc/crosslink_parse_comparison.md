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
