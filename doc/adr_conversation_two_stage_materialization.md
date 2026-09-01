# ADR: Optional Two-Stage Conversation Materialization

## Status

Accepted for the llm-wiki conversation namespace when explicitly configured.

## Decision

`kogwistar-llm-wiki` may construct its shared conversation engine with
`conversation_persistence_mode="two_stage"`. The conversation engine then uses
Kogwistar core's ADR-018 two-stage projection capability:

1. The canonical event and stage-1 readable projection are written first.
2. The node is addressable by ID while its embedding is pending, but it is not
   eligible for semantic/vector retrieval.
3. The existing durable `node_embedding` index job batches compatible pending
   rows and promotes only the current revision to the semantic projection.

Knowledge, workflow, wisdom, and derived-knowledge engines remain
single-stage unless a future, separately approved product policy opts them in.
This keeps immediately searchable knowledge entries unchanged while removing
embedding-provider latency from the conversation write hot path.

## Ownership

Kogwistar core owns canonical events, stage-1/stage-2 correctness, job leases,
revision-gated promotion, retry, and semantic-readiness gating. Llm-wiki owns
the narrow namespace choice and operational configuration. It must not create
a second materialization queue or bypass core's worker.

## Parser Integration

The workflow-layered parser can use the same opt-in mode for the parser's
conversation graph. Direct llm-wiki callers pass
`conversation_persistence_mode="two_stage"`; long-run callers set
`KOGWISTAR_LONGRUN_CONVERSATION_PERSISTENCE_MODE=two_stage` or choose the
`single_stage`/`two_stage` VS Code input. The parser's workflow and knowledge
engines remain single-stage. kg-doc-parser keeps its omitted-argument default
of `single_stage`, so this is an llm-wiki integration choice, not a parser-repo
default change.

For long-run experiments, opting into `two_stage` changes the corpus
fingerprint. The historical `single_stage` fingerprint is intentionally kept
stable so existing runs remain resumable; the mode is included in the emitted
configuration and parser trace for auditability.

## Benchmark Contract

`scripts/benchmark_conversation_two_stage.py` compares the two paths with
grounded mock `Node` payloads and a deterministic delayed embedding provider.
It reports:

- synchronous single-stage admission time and provider-call count;
- two-stage admission time and calls before draining;
- batch-promotion time, total time, final provider-call count, and promoted
  entity count.

The benchmark is evidence for reduced admission latency and provider request
count. It reports deferred promotion honestly rather than treating it as zero
cost, and does not impose a production-SLO threshold in unit tests.

## Invariants

- Conversation history remains readable before promotion.
- Pending rows do not appear in semantic retrieval.
- A worker promotes only the current canonical revision.
- A failed or delayed embedding never rolls back canonical conversation
  history.
- Enabling conversation two-stage persistence does not change the default
  single-stage behavior of other graph namespaces.
