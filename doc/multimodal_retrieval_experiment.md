# Multimodal Retrieval Experiment

## Scope

This experiment compares two uses of the existing native multimodal projection
plane:

1. **Synchronous recall:** the main worker waits for native text/image recall.
2. **Asynchronous sidecar:** the same recall runs behind a bounded,
   reference-first evidence feed while the main worker performs text/graph
   work.

The sidecar is a retrieval worker, not a second source of graph truth or an
autonomous conversational agent. Canonical source, revision, provenance, ACL,
and graph mutations remain owned by the existing host path.

## Implemented Surface

- `synchronous_multimodal_recall(...)` measures the blocking baseline.
- `MultimodalRetrievalSidecar` creates bounded `EvidenceSubscription` feeds.
- Events carry run, subscription, origin epoch, sequence, workspace, source
  revision, modality, profile, score, and locator references.
- `START`, `REFINE`, `CLOSE_FEED`, and `CANCEL` are represented by the feed
  lifecycle methods.
- `REFINE` starts a subsequent retrieval stage with a narrower source scope.
- `CLOSE_FEED` prevents admission even if the provider call finishes later.
- `CANCEL` closes admission immediately and cancels the provider task on a best
  effort basis.
- Duplicate references are deduplicated, and host validation runs before event
  admission. The validator is where existing ACL, namespace, revision, and
  profile checks are reused.
- Late events retain their origin epoch and can be admitted at the next
  explicit `drain()` checkpoint. Epoch mismatch alone does not discard them.

The implementation is intentionally application-local. No Kogwistar core graph
primitive was needed for this experiment.

## Fixture

`tests/fixtures/multimodal_retrieval/` contains a small synthetic design packet
with text notes, architecture and benchmark SVG artifacts, and a distractor.
The manifest marks all visual timing values as fixture data, not production
measurements. The architecture image is the late evidence that refines the
initial synchronous interpretation.

## Deterministic Results

Command:

```text
python scripts/benchmark_multimodal_retrieval_modes.py --delay-ms 80
```

Representative run:

| Metric | Synchronous | Async sidecar |
| --- | ---: | ---: |
| Main fast-path progress before MM evidence | 0 steps | 3 steps |
| Time to fast-path progress | blocked by MM | ~59 ms |
| Time to first MM evidence | ~91 ms | ~96 ms |
| Total scenario time | ~148 ms | ~96 ms |
| Expected visual target | yes | yes |
| Late correction | yes | yes |
| Admitted reference events | n/a | 1 |

The exact values vary slightly by host. The important result is that the
sidecar hides most of the expensive retrieval latency behind useful main-path
work; it does not make the encoder itself faster.

Focused regression command:

```text
python -m pytest \
  tests/unit/test_multimodal_projection.py \
  tests/unit/test_multimodal_evidence_projection.py \
  tests/unit/test_multimodal_remote.py \
  tests/unit/test_multimodal_runtime.py \
  tests/integration/test_profile_scoped_vector_backends.py \
  tests/unit/test_multimodal_retrieval_experiment.py \
  tests/unit/test_multimodal_retrieval_fixture.py \
  -q -p no:cacheprovider
```

Result: **66 passed, 2 skipped**. The skips are optional native-model paths.

The existing provider-free encoder benchmark also remains available through
`scripts/benchmark_multimodal.py`. Its fake profile measures API/batching
overhead only, not semantic retrieval or model inference.

## Native Model Status

No real Qwen3-VL/Ovis inference result is claimed for this run. The host has an
RTX 3080, but the local Hugging Face cache does not contain a complete usable
native checkpoint. A real comparison still requires one of:

- a complete local Qwen3-VL checkpoint;
- a running, profile-pinned embedding service; or
- a pinned vLLM endpoint with the matching profile.

The real-model run must use the same profile, projection store, source scope,
limits, and fixture for both modes.

## Recommendation

Keep the sidecar as a bounded retrieval worker. The deterministic evidence
supports the latency hypothesis and proves that late visual evidence can refine
an earlier conclusion without being discarded solely because it is late.

Before production use, add service-backed tests for profile/ACL validation,
real cross-modal ranking, provider failure metrics, queue backpressure, and
cold/warm model measurements. Do not add a separate reasoning model until
retrieval-only behavior fails to provide sufficient evidence.
