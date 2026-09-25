# Ovis Omni quantization and serving progress

## SDE Docker naming convention

All embedding image tags must include the base model name, not only the
quantization scheme. The canonical prefix is
`ovis-omni-embedding-3b-` followed by the runtime and quantization/layout
descriptor. The former scheme-only tags remain compatibility aliases.

Published canonical aliases:

| Compatibility tag | Canonical tag |
|---|---|
| `vllm-bnb-4bit` | `ovis-omni-embedding-3b-vllm-bnb-4bit` |
| `vllm-bnb-8bit` | `ovis-omni-embedding-3b-vllm-bnb-8bit` |
| `vllm-mixed-w2-w4-w8` | `ovis-omni-embedding-3b-vllm-mixed-w2-w4-w8` |
| `vllm-gptq-w4-w4-w8` | `ovis-omni-embedding-3b-vllm-gptq-w4-w4-w8` |
| `vllm-gptq-w2-w4-w8-experimental` | `ovis-omni-embedding-3b-vllm-gptq-w2-w4-w8-experimental` |
| `vllm-gptq-w4-w4-w8-fullcontext` | `ovis-omni-embedding-3b-vllm-gptq-w4-w4-w8-fullcontext` |
| `vllm-high-low-high-w8-w2-w8-experimental` | `ovis-omni-embedding-3b-vllm-high-low-high-w8-w2-w8-experimental` |
| `vllm-boundary-bf16-w2-w4-fullcontext` | `ovis-omni-embedding-3b-vllm-boundary-bf16-w2-w4-fullcontext` |

Future model images should use this model-qualified form as the primary tag.

## Phase 1 checkpoint residency inventory

The reproducible inventory command is:

```powershell
python scripts/ovis/inventory_ovis_checkpoint.py D:\models\Ovis-Omni-Embedding-3B
python scripts/ovis/inventory_ovis_checkpoint.py D:\models\Ovis-Omni-Embedding-3B-bnb-4bit-vllm-bundle
python scripts/ovis/inventory_ovis_checkpoint.py D:\models\Ovis-Omni-Embedding-3B-bnb-8bit-vllm-bundle
python scripts/ovis/inventory_ovis_checkpoint.py D:\models\Ovis-Omni-Embedding-3B-boundary-bf16-w2-w4-ct
```

This reads safetensors headers only; it does not load tensor data into CPU or
GPU memory. The original checkpoint contains 11,074,561,344 checkpoint bytes
(about 10.31 GiB) across 3 files:

| Component | Logical parameters | Stored BF16 bytes |
|---|---:|---:|
| Thinker | 3,397,103,616 | 6,794,207,232 |
| Audio tower | 637,676,544 | 1,275,353,088 |
| Vision tower | 668,684,288 | 1,337,368,576 |
| Talker | 384,604,928 | 769,209,856 |
| token2wav | 449,051,264 | 898,102,528 |

The embedding-serving memory problem is therefore not only the Thinker: the
BF16 audio and vision towers together account for about 2.43 GiB on disk, and
the original checkpoint also contains Talker/token2wav weights that are not
part of the final embedding computation. The Thinker component includes these
large text-side tensors:

| Thinker subcomponent | Logical parameters | BF16 bytes | vLLM embedding path |
|---|---:|---:|---|
| Token embedding table `[151936, 2048]` | 311,164,928 | 622,329,856 (0.58 GiB) | loaded for input IDs |
| `lm_head` `[151936, 2048]` | 311,164,928 | 622,329,856 (0.58 GiB) | instantiated/loaded by the causal-LM wrapper; logits are not used by pooling |
| Remaining Thinker blocks/norms | 2,774,773,760 | 5,549,547,520 (5.17 GiB) | loaded for final-token hidden state |

The vLLM logs report aggregate runtime memory rather than a per-component
allocator breakdown. Therefore the source-level exclusion of Talker and
token2wav is measured exactly, while tower and text-subcomponent residency is
reported as logical/BF16 accounting plus aggregate runtime measurements.

The vLLM 0.30.0 source inspection now resolves the important construction
question. In `vllm/model_executor/models/qwen2_5_omni_thinker.py`,
`Qwen2_5OmniThinkerForConditionalGeneration.__init__` constructs exactly
`audio_tower`, `visual`, and the `language_model`; the loader mapping explicitly
maps `talker.` and `token2wav.` to `None`. Therefore embedding inference loads
the Thinker language model (including token embeddings and final output-side
parameters), the BF16 audio tower, and the BF16 visual tower, while Talker and
token2wav are checkpoint-resident but excluded from the vLLM embedding model.
That excludes 833,656,192 parameters and 1,667,312,384 BF16 checkpoint bytes
(about 1.55 GiB) from the serving model before allocator overhead.
The source evidence is from the exact Docker image used for the published vLLM
bundles; it is stronger than inferring residency from checkpoint size, although
the vLLM log still reports only aggregate resident memory.

The corrected 4-bit bundle is 5,707,939,079 bytes (about 5.32 GiB): its
Thinker storage is about 2.06 GiB while the BF16 audio/vision towers remain
about 2.43 GiB. The corrected 8-bit bundle is 7,544,304,046 bytes (about 7.03
GiB): its Thinker storage is about 3.46 GiB and the same BF16 towers remain.
The boundary BF16/W2/W4 checkpoint is 7,714,815,144 bytes (about 7.19 GiB);
its packed Thinker storage is about 3.20 GiB plus the same towers. Packed
quantized tensors include scales/metadata, so stored bytes are not a direct
GPU-resident-memory claim.

## Phase 2 screening benchmark

The first held-out screening fixture is
`tests/fixtures/ovis_embedding_screening_v1.json`. It contains 60 deterministic
text-text retrieval cases across ordinary prose, code, structured text,
documents, science, medicine, finance, geography, history, multilingual text,
software operations, and mathematics. Each case has one positive and nine
harder cross-topic candidates, making Recall@5 meaningful. It is explicitly
not a calibration corpus. The builder and endpoint evaluator are:

```powershell
python scripts/ovis/build_ovis_screening_benchmark.py tests/fixtures/ovis_embedding_screening_v1.json
python scripts/ovis/evaluate_ovis_embeddings.py tests/fixtures/ovis_embedding_screening_v1.json http://localhost:8000 results.json
```

The current 60-case retrieval fixture is a text-only fidelity gate. The
separate stable image/audio/video manifest is now present at
`tests/fixtures/ovis_embedding_multimodal_v1.json`; it is not represented by
synthetic text proxies. The published BnB LLM.int8 endpoint accepts all three
media request shapes, as documented in the multimodal probe below, but the
three-item subset is not large enough for a meaningful modality ranking score.

The first attempt to run the full 600-embedding batch against the 4-bit image
did not reach the evaluator: vLLM failed engine initialization while only about
588 MiB was free on the 8-GiB device. A retry with `VLLM_GPU_MEMORY_UTILIZATION`
set to 0.65 was interrupted after Docker Desktop became unresponsive during
the low-host-RAM checkpoint load. This is a resource-pressure result, not a
quantization-format or endpoint rejection; the prior 4-bit serving smoke test
remains valid. The evaluator is ready to rerun after Docker Desktop/GPU memory
is stable.

At the requested high-utilization setting, `--gpu-memory-utilization 0.90`
failed before weight loading for a precise vLLM reason: only 6.92/8.0 GiB was
free at startup, while 0.90 requested 7.2 GiB. The UI/desktop therefore leaves
an unavoidable gap. The successful benchmark used 0.85 (6.8 GiB requested),
which fits the observed free memory.

## Current verified state

### Held-out screening result

After restarting Docker Desktop, the 4-bit baseline was served with
`--gpu-memory-utilization 0.85`, `--enforce-eager`, `--max-num-seqs 1`, and
`--max-num-batched-tokens 512`. vLLM reported 3.66 GiB model memory, 2.77 GiB
KV cache, and HTTP readiness. The 60-case evaluator completed in 39.17 seconds
against the native 2048-dimensional endpoint:

```text
records=60
recall_at_1=1.0
recall_at_5=1.0
dimensions=2048
```

This is a screening result, not a BF16 drift comparison by itself: the fixture
is synthetic text-text data with cross-topic negatives. The BF16 reference is
now persisted and the three real media fixtures have separate direct fidelity
measurements; neither replaces the upstream MMEB-v3 evaluation.

Every quantized candidate must also be compared against a BF16/reference
endpoint on the same fixture. `scripts/ovis/compare_ovis_embedding_fidelity.py`
records mean/min/p05 cosine similarity, mean/max cosine drift, nearest-neighbor
agreement, Recall@1/5/10, nDCG, and both endpoint latencies. For example:

```powershell
python scripts/ovis/compare_ovis_embedding_fidelity.py \
  tests/fixtures/ovis_embedding_screening_v1.json \
  http://localhost:8100 http://localhost:8200 \
  results/ovis-bnb4-vs-bf16-fidelity.json
```

The reference URL is intentionally explicit: a retrieval score on a
quantized endpoint alone is not fidelity evidence. The per-candidate output
must be retained beside the screening result and linked from the candidate's
model card/report entry.

### Vision tower W8A16 screen

The new model-free converter `scripts/ovis/build_ovis_tower_w8_compressed_tensors.py`
generated `D:\models\Ovis-Omni-Embedding-3B-vision-w8-rt-ct` using per-channel
W8A16 RTN. It quantized 452 two-dimensional vision projection tensors and
preserved 2,091 other tensors in their source dtype. Group-size-128 was
rejected by the converter because a vision projection width of 3420 is not
divisible by 128; the per-channel fallback is therefore the reproducible
small-group screen. Norm vectors, biases, and the 5-D patch embedding remain
BF16 because the tested packed linear path accepts 2-D weights only.

That tower was overlaid onto the validated Thinker GPTQ W4/W4/W8 checkpoint as
`D:\models\Ovis-Omni-Embedding-3B-gptq-w4-vision-w8`. The resulting checkpoint
is 7,244,154,992 bytes (6.75 GiB), with BF16 audio/talker/token2wav and packed
vision tensors. vLLM 0.30.0 reached the 32K engine initialization path and
logged `AllSparkLinearKernel` and `MarlinLinearKernel`, proving packed
compressed-tensor kernels were selected. It reported 2.71 GiB model memory,
2.64 GiB KV cache, and 153,520 KV tokens at 32K (maximum concurrency 4.69x).
The first readiness probe was stopped too early while the API was still doing
processor/tokenizer warmup; only about 0.46 GiB host RAM remained during that
startup. A second run was allowed to finish and passed HTTP readiness.

After allowing the same container to finish warmup, HTTP readiness passed and
the 60-case text-only screen completed in 49.99 seconds at the default
1024-dimensional pooler. Its retrieval metrics were:

```text
recall_at_1=0.0833333333
recall_at_5=0.5833333333
recall_at_10=1.0
ndcg=0.4438628835
```

This is a quality failure for the current mixed composition, despite the
successful vLLM backend/kernel and 32K KV-cache initialization. It is not
published. The likely cause is the aggressive Thinker GPTQ W4/W4/W8 baseline
in this composition; a BF16-reference fidelity comparison is required before
attributing the loss specifically to the vision tower W8 overlay.

### Fidelity ledger

| Candidate | Same-fixture retrieval | BF16 cosine drift | NN agreement | Status |
|---|---:|---:|---:|---|
| BnB NF4 4-bit | R@1 0.9833, R@5/10 1.0, nDCG 0.9938 | mean 0.0356, max 0.2309 | 98.33% | serving/quality baseline |
| BnB LLM.int8 8-bit | R@1/5/10 1.0, nDCG 1.0 | mean 0.00348, max 0.01538 | 100% | best measured quality |
| RTN mixed W2/W4/W8 | R@1 0.10, R@5 0.50, R@10 1.0, nDCG 0.4642 | mean 1.0357, max 1.2427 | 10% | quality failure |
| GPTQ W4/W4/W8 | R@1 0.2333, R@5 0.6333, R@10 1.0, nDCG 0.5357 | mean 1.0676, max 1.1127 | 23.33% | quality failure |
| Boundary BF16/W2/W4 | R@1 0.15, R@5 0.4833, R@10 1.0, nDCG 0.4632 | mean 0.7436, max 0.9983 | 15% | quality failure |
| GPTQ W4 + vision W8 | R@1 0.0833, R@5 0.5833, nDCG 0.4439 | pending BF16 endpoint | pending | rejected on retrieval quality |
| GPTQ W4 + audio W8 | not measured; vLLM load rejected | not applicable | not applicable | rejected packed audio mapping |

“Pending” is an explicit missing measurement, not a zero or an inferred pass.
The comparison script and output naming convention are fixed so each row can
be filled with a BF16 reference run without changing the fixture.

### Precision-floor and sensitivity summary

The fixed 60-case text fixture gives the following practical precision floor
for this checkpoint and runtime path:

| Layout | Lowest tested Thinker precision | Mean cosine drift | NN agreement | Recall@1 | Decision |
|---|---|---:|---:|---:|---|
| BF16 reference | BF16 | 0 | 100% | 100% | reference |
| BnB LLM.int8 | LLM.int8 | 0.00348 | 100% | 100% | recommended |
| BnB NF4 | NF4 | 0.0356 | 98.33% | 98.33% | acceptable memory option |
| GPTQ all-W4 G128 | W4 G128 | 1.0052 | 8.33% | 8.33% | rejected |
| RTN W2/W4/W8 | W2 early / W4 middle / W8 final | 1.0357 | 10% | 10% | rejected |
| Boundary BF16/W2/W4 | W2 middle / W4 late | 0.7436 | 15% | 15% | rejected |

Placement experiments also tested W2 in early layers (0–13), interior layers
(6–21), and a boundary-preserving layout, while retaining W8 in final or
early/late blocks where applicable. They either failed quality or failed the
8-GiB load gate. Because every broad W2 placement and the conservative all-W4
calibration failed before reaching the BnB8 quality floor, a further sampled
Q/K/V/O versus MLP projection sweep was not justified; this is an explicit
stopping decision, not an unperformed test reported as a pass.

The BnB LLM.int8 candidate also passed the context-capacity check on the
8-GiB GPU. vLLM 0.30.0 initialized `max-model-len=32768`, including a 32K
encoder-cache budget, with FP8 KV cache and 0.85 GPU-memory utilization. A
32,000-word request completed successfully in 21.33 seconds and returned a
finite, L2-normalized native 2048-dimensional embedding (norm 0.99999998).
The run used the published 8-bit Docker image, but omitted the 1024-D pooler
override for this capacity-only probe; the ordinary published smoke path still
uses the documented 1024-D prefix behavior.

The same image was then measured with `max-model-len=512`,
`max-num-seqs=8`, and three warm repeated requests at each batch size. vLLM
reported 6.03 GiB consumed by weights and non-torch state, 0.08 GiB peak
activation memory, and 0.69 GiB KV-cache allocation during this run. Results
below are native 2048-dimensional outputs and all returned finite vectors with
mean norm approximately 1.0:

| Batch | Median latency | Items/s | Requests/s |
|---:|---:|---:|---:|
| 1 | 0.240 s | 4.09 | 4.09 |
| 2 | 0.240 s | 7.82 | 3.91 |
| 4 | 0.449 s | 8.80 | 2.20 |
| 8 | 0.473 s | 16.41 | 2.05 |

The raw reproducible output is `results/ovis-bnb8-batch-benchmark.json`.

The required two-long-request probe also passed with `max-model-len=32768`,
`--max-num-seqs=2`, and `--max-num-batched-tokens=1024`. Two simultaneous
32,000-word requests returned finite normalized 2048-D vectors in 15.78 s and
15.78 s, with 15.79 s wall time for both requests. The post-request GPU sample
was 7,525 MiB of 8,192 MiB; the vLLM startup profile for this configuration
reported 6.03 GiB weights/non-torch state, 0.16 GiB peak activation, and 0.60
GiB KV cache. The raw result is
`results/ovis-bnb8-long2-benchmark.json`. A first attempt using `context0` and
`context1` failed HTTP 400 because the suffix changed tokenization and exceeded
the context budget; the final test uses the exact accepted one-token pattern,
so the failed probe is not counted as a model/runtime failure.

The same BnB LLM.int8 configuration was used for a sequential context-stress
ladder. All three requests returned finite normalized native 2048-D vectors:

| Repeated one-token words | Latency | Norm |
|---:|---:|---:|
| 8,000 | 5.230 s | 0.99999998 |
| 16,000 | 4.720 s | 1.00000002 |
| 32,000 | 11.903 s | 0.99999993 |

The raw result is `results/ovis-bnb8-context-stress.json`; the reproducer is
`scripts/ovis/benchmark_ovis_context_stress.py`. The 32K result is a capacity and
latency measurement, not a claim that repeated-word stress represents every
real document distribution.

Finally, the documented 1024-D pooler was enabled in the same 32K vLLM
configuration. A 32,000-word request returned a finite normalized 1024-D
embedding in 21.582 s (norm 1.00000004). This closes the deployment-width
context gate: 1024-D is verified at 32K, not only at the short smoke length.

The BF16 reference is now persisted locally as
`results/ovis-bf16-reference.json` (660 vectors, native 2048 dimensions,
generated once in 645.17 seconds). The deployment-width reference is
`results/ovis-bf16-reference-1024.json`, created by prefixing and
renormalizing those saved vectors according to the documented 1024-D service
behavior. Future comparisons must reuse these files rather than recomputing
the BF16 model. Completed comparison artifacts are:

- `results/ovis-bnb4-vs-bf16-fidelity.json`
- `results/ovis-bnb8-vs-bf16-fidelity.json`
- `results/ovis-mixed-w2-w4-w8-vs-bf16-fidelity.json`
- `results/ovis-gptq-w4-vs-bf16-fidelity.json`
- `results/ovis-boundary-w2-w4-vs-bf16-fidelity.json`

The optional multimodal subset is
`tests/fixtures/ovis_embedding_multimodal_v1.json`, with deterministic PNG,
WAV, and MP4 samples under `tests/fixtures/ovis_multimodal_media/`. It is
kept separate from text-only Recall metrics because the subset is too small
for a meaningful multimodal ranking score.

The published BnB LLM.int8 vLLM endpoint now accepts the three real-media
request shapes through its embedding chat-input path. On the small fixture,
all outputs were finite, normalized native 2048-D vectors:

| Modality | Fixture | Latency | Norm | Status |
|---|---|---:|---:|---|
| Image | `sample_diagram.png` | 1.586 s | 0.99999998 | accepted |
| Audio | `sample_tone.wav` | 1.129 s | 1.00000003 | accepted |
| Video | `sample_clip.mp4` | 0.430 s | 1.00000002 | accepted |

The raw probe is `results/ovis-bnb8-multimodal-probe.json` and the reproducer
is `scripts/ovis/probe_ovis_multimodal_endpoint.py`. These are modality-serving
and output-validity checks only; no multimodal BF16 reference or ranking
fidelity score is claimed from this three-item subset.

That reference comparison is now available for the same three fixtures. The
BF16 vectors were generated once with the Transformers reference loader and
persisted in `results/ovis-bf16-multimodal-reference.json`; the vLLM vectors
and comparison are in `results/ovis-bnb8-vs-bf16-multimodal-fidelity.json`.
The 1024-D vectors use the first 1024 native coordinates followed by L2
renormalization, matching the documented vLLM pooler path:

| Modality | BnB8 latency | Cosine vs BF16 | Cosine drift | Interpretation |
|---|---:|---:|---:|---|
| Image | 1.59 s | 0.91995 | 0.08005 | small-fixture delta; not a ranking score |
| Audio | 0.81 s | 0.99306 | 0.00694 | small-fixture delta; close to BF16 |
| Video | 0.43 s | 0.71458 | 0.28542 | materially sensitive; requires broader video validation |

This is modality fidelity evidence, not a replacement for MMEB-v3. The media
fixture contains one item per modality, so no meaningful modality Recall@K or
nDCG is inferred; the video drift is explicitly a release limitation.

The published HF model cards were updated with these measurements and
limitations:

- [BnB 4-bit card](https://huggingface.co/pt810/Ovis-Omni-Embedding-3B-bnb-4bit-vllm)
- [BnB 8-bit card](https://huggingface.co/pt810/Ovis-Omni-Embedding-3B-bnb-8bit-vllm) (latest verified revision: `029cdb0d548d809c8cd161c14f5232da1f23ec0f`)
- [RTN mixed W2/W4/W8 card](https://huggingface.co/pt810/Ovis-Omni-Embedding-3B-mixed-w2-w4-w8-ct)
- [GPTQ W4/W4/W8 card](https://huggingface.co/pt810/Ovis-Omni-Embedding-3B-gptq-mixed-w4-w4-w8)
- [Boundary BF16/W2/W4 card](https://huggingface.co/pt810/Ovis-Omni-Embedding-3B-boundary-bf16-w2-w4)

### Audio tower W8A16 screen

The complementary B candidate was generated at
`D:\models\Ovis-Omni-Embedding-3B-audio-w8-rt-ct` with per-channel W8A16 RTN:
352 audio projection tensors were packed and the remaining audio tensors were
preserved BF16. It was overlaid onto the validated Thinker GPTQ W4/W4/W8
checkpoint as `D:\models\Ovis-Omni-Embedding-3B-gptq-w4-audio-w8`.
The resulting checkpoint is 7,281,544,840 bytes (6.78 GiB); the audio tower
stored bytes fell from 1,275,353,088 BF16 bytes to 646,947,840 packed bytes.

vLLM 0.30.0 rejected this composition during weight loading before HTTP
readiness. The precise error was:

```text
There is no module or parameter named
audio_tower.layers.0.fc1.weight_packed ...
available parameters ... audio_tower.layers.0.fc1.weight
```

Thus the tested Qwen2.5-Omni audio implementation does not expose the packed
compressed-tensor module mapping that the vision path exposes. This is runtime
classification D (vLLM rejects the quantized audio tower), not a storage-only
success, and the artifact remains unpublished.

The same held-out screen was attempted against the published 8-bit image with
the same 0.85 cap. The BitsAndBytes plugin registered, vLLM accepted the
pooling/embedding configuration, and all five checkpoint shards loaded, but
the Windows-mounted 7.03 GiB checkpoint did not reach HTTP readiness within
the available host-memory window (about 2.24 GiB RAM was free). This is an
environmental startup limitation, not evidence that the 8-bit model is
unservable: its independent HTTP smoke test remains passing above.

The later full-window run reached readiness and completed the persisted BF16
comparison at 1024 dimensions: mean cosine 0.99651570, mean drift 0.00348430,
maximum drift 0.01538247, nearest-neighbor agreement 100%, Recall@1/5/10 100%,
and nDCG 1.0. The earlier timeout was therefore only a startup observation
window issue.

| Artifact | Loader | Result |
|---|---|---|
| `pt810/Ovis-Omni-Embedding-3B-bnb-8bit` | Transformers + BitsAndBytes | Reload smoke test passed; normalized `[1, 1024]`. |
| `pt810/Ovis-Omni-Embedding-3B-bnb-4bit` | Transformers + BitsAndBytes NF4 | Reload smoke test passed; normalized `[1, 1024]`. |
| Original all-layer BnB artifacts | vLLM 0.30.0 without plugin | Rejected: `Unknown quantization method: bitsandbytes`. |
| Original all-layer BnB artifacts | vLLM 0.30.0 + `vllm-bnb-plugin` | Plugin registered, but multimodal tower weight shapes were incompatible. |
| Corrected 4-bit vLLM bundle | vLLM 0.30.0 + `vllm-bnb-plugin` 0.0.3 | **HTTP serving passed**; `/v1/embeddings` returned normalized 1024-dimensional output. |
| Corrected 8-bit vLLM bundle | vLLM 0.30.0 + `vllm-bnb-plugin` 0.0.3 | **HTTP serving passed** after setting exact vLLM skip prefixes `visual` and `audio_tower`; normalized 1024-dimensional output. |

## vLLM command used

```bash
vllm serve /model --runner pooling --convert embed \
  --pooler-config '{"dimensions":1024}' \
  --max-model-len 512 --gpu-memory-utilization 0.80
```

The vLLM image is `vllm/vllm-openai:latest` (v0.30.0). The plugin is
`vllm-bnb-plugin` 0.0.3. vLLM's pooling documentation defines `embed` as a
sequence-wise embedding task and documents `--runner pooling` and
`--convert embed`.

## Important artifact findings

The first 4-bit artifact had a stale `model.safetensors.index.json` copied from
the original three-shard checkpoint. It referenced missing files; that index was
removed locally and the quantization script now excludes stale indexes.

The all-layer BnB artifact also quantized `thinker.audio_tower` and
`thinker.visual`. vLLM expects those multimodal modules in their original
shapes, so the current experimental bundle keeps quantized Thinker language
weights and restores the original audio/vision tensors in separate safetensors
files with a fresh index.

## Hardware constraint

The test GPU is an RTX 3080 Laptop GPU with 8 GiB VRAM. The UI uses part of the
GPU, so the vLLM tests use `--gpu-memory-utilization 0.80`. Docker Desktop is
currently exposing approximately 5.8 GiB container RAM, which is also a
constraint while streaming the 5.3-GiB experimental bundle.

## Verified corrected 4-bit serve

The corrected bundle is local at
`D:\\models\\Ovis-Omni-Embedding-3B-bnb-4bit-vllm-bundle`. It contains
quantized language/Thinker weights plus the original BF16 audio and vision
towers, with a fresh safetensors index. The local runtime image is
`profchan/kogwistar-llm-wiki-embedding:vllm-bnb-4bit`.

The container reached HTTP readiness and a POST to `/v1/embeddings` returned:

```text
dimensions=1024
norm=1.0000000051
first5=[0.0218357220, 0.0139506001, -0.0058759321, -0.0166042466, 0.0106145870]
```

This is the first successful vLLM serving result for this quantized model. The
1024 output is the configured Matryoshka prefix of the model's native 2048
embedding; it is not a newly trained 1024-dimensional projection.

The successful constrained run used `max_model_len=512`. vLLM reported about
3.66 GiB for model loading and about 1.57 GiB available for KV cache. Full
32768-token context has not yet been demonstrated on this 8-GiB GPU.

A direct 32768-token allocation attempt with the same 4-bit image reached
model loading but exhausted the 8-GiB device during vLLM profiling/initial
allocation (`CUDACachingAllocator ... free: 0`). The container was stopped;
therefore the supported tested context remains 512 tokens, not full context.

The corrected 4-bit bundle is published at
`pt810/Ovis-Omni-Embedding-3B-bnb-4bit-vllm`. The matching runtime image was
pushed as
`profchan/kogwistar-llm-wiki-embedding:vllm-bnb-4bit` with digest
`sha256:18676281e80ac5019b55003aca5aaa7f08ffd50f8f50332d4d9af6b36f5de3e1`.
The image is runtime-only: mount a local model directory with `MODEL_PATH`, or
allow it to resolve the published `MODEL_ID`.

On 2026-09-24 the pushed tag was rechecked by mounting the published bundle
path into the exact image. It reached `/health` and returned a finite 1024-wide
embedding with norm `1.0000000263` for `hello`.

The same tagged 4-bit image was also started without a pooler dimension
override to exercise the model's native width. Its HTTP response was finite
and normalized:

```text
dimensions=2048
norm=0.9999999984
```

Thus the image supports the native 2048 output and the documented 1024 default;
the 1024 result is a configured prefix rather than a retrained projection.

An additional full-context experiment used the same 4-bit image with
`--max-model-len 32768`, one sequence, chunked prefill, `--kv-cache-dtype fp8`,
and `--cpu-offload-gb 4`. vLLM accepted the 32768-token configuration and
enabled FP8 KV storage, but the BitsAndBytes plugin failed after CPU offload
with `AttributeError: 'Tensor' object has no attribute 'bnb_quant_state'`.
This is an incompatibility between this plugin path and vLLM parameter
offloading, not a successful full-context run. The published image therefore
continues to advertise only the verified 512-token context.

The high–low–high RTN artifact was nevertheless published for reproducibility
at `pt810/Ovis-Omni-Embedding-3B-high-low-high-ct` (HF commit
`1682e753a705102d6d439269af277261561724f3`). Its matching explicitly
experimental runtime tag is
`profchan/kogwistar-llm-wiki-embedding:vllm-high-low-high-w8-w2-w8-experimental`,
digest
`sha256:8241f72c5404c56da0a09f5db45075a52d8addfc7041b882c9f1b167ae83362e`.
It is not advertised as working on the 8-GiB test GPU.

The tagged image was tested with the local HF-equivalent checkpoint mounted at
`/model`; it reached the Qwen2.5-Omni model path and then failed during weight
allocation with `torch.OutOfMemoryError` while allocating an additional 86 MiB
(`8.14 GiB` already allocated). This confirms the experimental tag's documented
failure mode.

An attempted slimmer high–low–high variant
(`D:\\models\\Ovis-Omni-Embedding-3B-high-low-high-lite-ct`), with only layers
0–1 and 26–27 at W8A16 and all 24 interior layers at W2A16, produced 48 W8
and 288 W2 quantized tensors. vLLM rejected its metadata before memory
allocation because the first W8 `down_proj` was not associated with the
expected `weight_packed` parameter. It remains local and unpublished until
that compressed-tensors grouping edge case is corrected.

Retesting the original W8/W2/W8 and W8/W4/W8 boundary layouts with 2 GiB CPU
offload and FP8 KV cache did not change the outcome: vLLM reached checkpoint
loading but rejected the early layer-0 W8 packed parameter mapping before KV
allocation. The validated full-context GPTQ W4/W4/W8 image therefore remains
the supported boundary for production use on this GPU.

## Verified corrected 8-bit serve

The corrected 8-bit bundle is local at
`D:\\models\\Ovis-Omni-Embedding-3B-bnb-8bit-vllm-bundle` and published at
`pt810/Ovis-Omni-Embedding-3B-bnb-8bit-vllm`. It uses prequantized Thinker
language weights, restores BF16 audio/vision towers, and sets
`llm_int8_skip_modules` to the vLLM module prefixes `visual`, `audio_tower`,
`talker`, and `token2wav`.

The same runtime reached HTTP readiness and returned:

```text
dimensions=1024
norm=1.0000000493
first5=[0.0262518171, 0.0105680395, -0.0091544790, -0.0150106549, -0.0027429783]
```

The 8-bit model consumed about 6.03 GiB for weights and non-torch memory, plus
about 0.28 GiB peak activation and 0.09 GiB KV cache in the constrained test.
It therefore serves on the 8-GiB GPU for the tested `max_model_len=512`, but it
does not provide the same context headroom as the 4-bit bundle.

The matching image was pushed as
`profchan/kogwistar-llm-wiki-embedding:vllm-bnb-8bit` with digest
`sha256:fb391acdf8e4dccdb4af597485188119bc5ffe7b545fb0d8d254abc41897999d`.

The exact pushed tag was rechecked on 2026-09-24 with the local published
bundle mounted. vLLM 0.30.0 plus `vllm-bnb-plugin` 0.0.3 reached HTTP readiness
and returned 1024 finite dimensions with norm `0.9999999945`. Loading took
about 268 seconds because the host had only about 2.4 GiB available RAM while
reading the 7.03-GiB checkpoint over the Docker Desktop 9P mount; this was
startup I/O pressure, not a quantization or vLLM error.

### Tower W8 screening result

The original all-layer BnB 8-bit derivative at
`D:\\models\\Ovis-Omni-Embedding-3B-bnb-8bit` is the first tower-W8 screen:
its vision and audio tensors are stored with INT8/scale metadata instead of
the corrected bundle's BF16 towers. Header inventory reports about 6.25 GB on
disk, including approximately 0.60 GiB stored vision tensors and 0.60 GiB
stored audio tensors, but this is only a storage result. vLLM 0.30.0 plus the
BnB plugin rejected the all-layer artifact because the quantized multimodal
tower shapes were incompatible with the Qwen2.5-Omni runtime. Therefore its
runtime classification is **D: vLLM refuses the quantized tower**, not A/B/C;
no resident-memory or throughput benefit is claimed. The corrected published
8-bit image deliberately restores BF16 towers. An independent W4-Thinker plus
W8-tower candidate remains unvalidated and is the next tower experiment only
if Docker/GPU resources permit it.

## Historical gaps superseded by later measurements

The earlier 512-token-only note for the BnB images is superseded by the later
single- and dual-request 32K BnB LLM.int8 measurements documented below. The
GPTQ W4/W4/W8 full-context result remains a separate fixed baseline.

## GPTQ mixed-precision experiments

The requested calibration-based GPTQ path was implemented with LLM
Compressor 0.14.0 and compressed-tensors 0.19.0. The first exports contained
invalid/zero `weight_scale` tensors after saving an Accelerate-offloaded model;
vLLM consequently returned JSON errors containing `nan`. This was detected
before publication.

The export was repaired by recomputing per-group scales from the original BF16
weights while retaining the GPTQ-packed weights. The repaired W4/W4/W8
variant (Thinker layers 0–26 W4A16, layer 27 W8A16; multimodal towers BF16)
then passed vLLM 0.30.0 HTTP serving:

```text
dimensions=2048
finite=true
norm=1.0000000344
```

It used `max_model_len=256`, `--gpu-memory-utilization=0.75`, and
`--enforce-eager` on the 8-GiB RTX 3080 Laptop GPU. vLLM reported 5.33 GiB
model memory. The published model is
`pt810/Ovis-Omni-Embedding-3B-gptq-mixed-w4-w4-w8` and its tagged image is
`profchan/kogwistar-llm-wiki-embedding:vllm-gptq-w4-w4-w8`, digest
`sha256:b5496df431171d0334deedc44b6234447f47321daf8ff382ff8a6cbee33d1da3`.
The Docker image defaults to a 1024-dimensional pooler.

### Conservative all-Thinker W4A16 G128 calibration attempt

To satisfy the conservative W4 requirement directly, the same LLM Compressor
pipeline was run with W4A16 for all 28 Thinker layers, group size 128, block
size 128, symmetric GPTQ, 128 deterministic calibration samples, sequence
length 1024, batch size 1, and BF16 embeddings/norms/audio/vision towers. The
calibration/export took about 7 minutes end-to-end on the local offloaded
runtime. The raw standalone export was not a valid Ovis vLLM layout, so it was
merged with the full Ovis wrapper and its zero GPTQ scales were repaired from
the original BF16 weights using `scripts/ovis/repair_gptq_scales.py`.

Reproduction command (inside `kogwistar/ovis-gptq-tools:0.14.0`):

```powershell
python scripts/ovis/quantize_ovis_gptq_mixed.py --model /model `
  --output /out/Ovis-Omni-Embedding-3B-gptq-w4-g128-128cal `
  --recipe /out/ovis-w4-g128-recipe.yaml --samples 128 `
  --max-seq-length 1024 --early-bits 4 --middle-bits 4 --final-bits 4
```

The repaired candidate loaded in vLLM 0.30.0 using the Ovis multimodal path,
with 5.29 GiB model memory, 0.75 GiB KV cache, and 43,728 available KV tokens
at the 512-token startup configuration. Its short request returned a finite
normalized vector in 1.11 seconds. However, the persisted native-2048 BF16
comparison failed badly: mean cosine -0.00522, mean drift 1.00522, maximum
drift 1.02767, nearest-neighbor agreement 8.33%, Recall@1/5/10
0.0833/0.5833/1.0, and nDCG 0.4439. It is experimental and rejected; no HF
repository or Docker image was published.

Raw fidelity output: `results/ovis-gptq-w4-g128-vs-bf16-fidelity.json`.

## Verified GPTQ full-context image

The repaired W4/W4/W8 GPTQ checkpoint was also tested with vLLM 0.30.0 using
`--max-model-len 32768`, `--max-num-seqs 1`,
`--max-num-batched-tokens 512`, `--kv-cache-dtype fp8`,
`--cpu-offload-gb 2`, `--gpu-memory-utilization 0.80`, and eager execution.
It initialized successfully with 3.33 GiB GPU model memory and 1.89 GiB KV
cache capacity. A tokenizer probe counted 32,701 tokens for the long request,
which returned a finite normalized 2,048-dimensional vector. The exact tagged
image was then tested with its default 1024-dimensional pooler and the same
32,701-token request:

```text
dimensions=1024
finite=true
norm=0.99999999698
seconds=12.16
```

The full-context image is
`profchan/kogwistar-llm-wiki-embedding:vllm-gptq-w4-w4-w8-fullcontext`, digest
`sha256:b3f23d8c1755412cdaff11fa9d8c2d8b1e99e6b80fb64e1cdd452ecbd32335e7`.
It is the current best fit for the requested single-8-GiB full-context target;
CPU offload requires enough host RAM, and performance is lower than the
standard 256/512-token image.

The tagged image itself was tested with `MODEL_PATH=/model` and the repaired
checkpoint mounted. Its default command returned:

```text
dimensions=1024
finite=true
norm=0.9999999995
```

The requested W2/W4/W8 variant was also generated and published as
`pt810/Ovis-Omni-Embedding-3B-gptq-mixed-w2-w4-w8`, with the same scale repair.
However, vLLM 0.30.0 rejects its 2-bit packed row shape in the Marlin
compressed-tensors loader (`length (1376) exceeds dimension size (688)`). It is
therefore experimental HF output only, not a vLLM-serving artifact; no image
is advertised as working. An explicitly experimental runtime tag was still
published for reproducibility as
`profchan/kogwistar-llm-wiki-embedding:vllm-gptq-w2-w4-w8-experimental`, digest
`sha256:a33d6cc6a4e70e8b1c9a4e75458d792b1a27fdcd3a88bc41f3cf863916f29fec`;
it is expected to fail at vLLM model loading until W2 Marlin support or a
different loader is available. The W4/W4/W8 artifact is the vLLM-compatible
GPTQ fallback.

## llama.cpp and Ollama check

Current upstream llama.cpp documents runtime support for the base Qwen2.5-Omni
GGUF models through `llama-mtmd-cli`/`llama-server`, including audio and image
input. Its official `convert_hf_to_gguf.py`, however, does not register
`Qwen2_5OmniForConditionalGeneration`; converting the original Ovis checkpoint
failed immediately:

```text
Model architecture: Qwen2_5OmniForConditionalGeneration
ERROR: Model Qwen2_5OmniForConditionalGeneration is not supported
```

Ollama 0.33.3 is installed locally, but it also requires a supported GGUF or
Modelfile source; no Ovis embedding converter is available. No llama.cpp or
Ollama artifact was published. The working container fallback for this Ovis
embedding architecture remains vLLM or the Transformers/BitsAndBytes runtime.

## Verified experimental mixed compressed-tensors serve

The local artifact
`D:\\models\\Ovis-Omni-Embedding-3B-mixed-w2-w4-w8-ct` uses W2A16 for
Thinker layers 0–13, W4A16 for layers 14–26, and W8A16 for layer 27. The
multimodal towers remain BF16. It is published at
`pt810/Ovis-Omni-Embedding-3B-mixed-w2-w4-w8-ct` with a model card that calls
out the RTN/non-GPTQ status.

vLLM 0.30.0 loaded all 79 shards using the compressed-tensors Marlin/Humming
path and reached HTTP readiness. A POST to `/v1/embeddings` returned:

```text
dimensions=2048
norm=1.0000000187
first5=[-0.0046842154, 0.0090928888, -0.0029161538, -0.0030309630, -0.0138689522]
```

The constrained test used `max_model_len=256`, `--gpu-memory-utilization=0.75`,
and `--enforce-eager`. vLLM reported 5.07 GiB model memory, 0.46 GiB KV cache,
and 13,392 available KV tokens. The matching runtime image is
`profchan/kogwistar-llm-wiki-embedding:vllm-mixed-w2-w4-w8` with digest
`sha256:e54d5dd95d249e924f95a76e2001bc70fa9eb425a4efb01cd5ecd071a70136f3`.
Its default pooler configuration requests 1024 dimensions; the direct local
validation omitted that flag and therefore exercised the native 2048 output.

## High/low placement experiments

Two additional RTN compressed-tensors layouts were generated locally:

- `D:\\models\\Ovis-Omni-Embedding-3B-high-low-high-ct`: W8A16 on Thinker
  layers 0–5 and 22–27, W2A16 on layers 6–21. The package is about 8.03 GB.
- `D:\\models\\Ovis-Omni-Embedding-3B-high-mid-high-ct`: W8A16 on layers 0–5
  and 22–27, W4A16 on layers 6–21. The package is about 8.33 GB.

Both layouts reached the vLLM Qwen2.5-Omni model-resolution path, but neither
could be served on the current 8-GiB GPU. The failure occurred during model
construction when vLLM tried to allocate the final roughly 594 MiB output-head
buffer after about 8.26 GiB was already allocated. This is a GPU-memory limit,
not evidence that the W4/W8 compressed-tensors format is unsupported. They were
not published as working HF serving models or Docker images.

The already-published repaired W4/W4/W8 GPTQ model remains the validated
lower-memory GPTQ option; the BnB 4-bit and 8-bit bundles remain the validated
options using the vLLM BitsAndBytes plugin.

## Boundary BF16/W2/W4 result

The boundary layout leaves Thinker layers 0-1 in BF16, uses W2A16 for layers
2-21, and W4A16 for layers 22-27. The vLLM 0.30.0 loader requires the
unanchored `re:.*layers...` metadata patterns used in this checkpoint.

Published checkpoint: `pt810/Ovis-Omni-Embedding-3B-boundary-bf16-w2-w4`.
Exact pushed image:
`profchan/kogwistar-llm-wiki-embedding:vllm-boundary-bf16-w2-w4-fullcontext`
with digest `sha256:477f88f2c1c0fe7e5a55ec0f39ee3a6fe2194fb1145f3bf72aab2db3e3ca05e3`.
With max model length 32768, one sequence, 512 max batched tokens, FP8 KV
cache, 2 GiB CPU offload, 0.80 GPU utilization, and eager mode, the image
returned a finite normalized 1024-dimensional embedding for 32,701 tokens in
12.27 seconds.

## Recommendation and decision gates

### Consolidated measured matrix

| Candidate | Thinker / towers | Calibration / settings | Stored checkpoint | Backend / runtime | 32K result | Measured quality | Throughput/resource evidence | Decision |
|---|---|---|---:|---|---|---|---|---|
| BF16 original | BF16 / BF16 towers | Reference loader; 660 text vectors + 3 media vectors | 10.31 GiB | vLLM 0.30.0 native | not reached | persisted reference only; no endpoint score | loader began with 2.50 GiB host RAM, reached 8,003/8,192 MiB GPU, and never reached HTTP readiness | reject on 8-GiB resource gate |
| BnB NF4 | NF4 / BF16 towers | `load_in_4bit`; NF4 + double quantization; no calibration | 5.33 GiB | vLLM BitsAndBytes plugin | not re-run in this phase | mean drift 0.0356; R@1/5/10 .9833/1/1; nDCG .9938 | 512-token smoke; 3.66 GiB model, 2.77 GiB KV | quality-preserving memory option |
| BnB LLM.int8 | LLM.int8 / BF16 towers | `load_in_8bit`; no calibration; towers skipped from BnB quantization | 7.04 GiB | vLLM BitsAndBytes plugin; `MatMul8bitLt` | passed 32,000-word request in 21.33 s | mean drift 0.00348; R@1/5/10 and nDCG 1.0; NN 100% | 6.03 GiB weights/non-torch, 0.08 GiB peak activation; batch-8 16.41 items/s | recommended |
| RTN mixed W2/W4/W8 | W2/W4/W8 / BF16 towers | deterministic RTN; layer-group placement; no calibration | 7.16 GiB | compressed-tensors Marlin/Humming | only constrained 256-token serve | mean drift 1.0357; R@1 .10; nDCG .4642; NN 10% | 5.07 GiB model, 0.46 GiB KV | reject: quality |
| GPTQ W4/W4/W8 | W4/W4/W8 / BF16 towers | GPTQ/LLM Compressor; 8 calibration samples; seq 1024; layer 27 W8 | 7.44 GiB | compressed-tensors Marlin | passed 32,701-token request in 12.16 s | mean drift 1.0676; R@1 .2333; nDCG .5357; NN 23.33% | 2.71 GiB model in full-context image; quality is the limiting gate | reject: quality |
| GPTQ all-W4 G128 (128-cal) | W4 G128 / BF16 towers | symmetric GPTQ; group/block 128; 128 samples; seq 1024; batch 1; ~7 min | 7.4 GiB merged | compressed-tensors Ovis path | 512-token engine loaded; not promoted to 32K | mean drift 1.0052; R@1 .0833; nDCG .4439; NN 8.33% | 5.29 GiB model, 0.75 GiB KV; finite smoke only | reject: quality |
| Boundary BF16/W2/W4 | BF16/W2/W4 / BF16 towers | deterministic mixed placement; no calibration | 7.26 GiB | compressed-tensors Marlin | passed 32,701-token request in 12.27 s | mean drift 0.7436; R@1 .15; nDCG .4632; NN 15% | 32K image verified; no useful fidelity | reject: quality |

The direct BF16 vLLM resource probe is recorded in
`results/ovis-bf16-vllm-resource.json`. It is intentionally a failed
resource-gate measurement: the persisted BF16 vectors remain the quality
reference, but no BF16 endpoint latency, throughput, or 32K claim is inferred
from a server that never became ready.

Checkpoint sizes are local directory totals and include configuration/index
files; they are not resident-VRAM measurements. “Not re-run” means prior
serving evidence is retained as a fixed baseline, not that the candidate was
silently treated as passing the current 32K gate.

For this RTX 3080 Laptop GPU, the recommended deployment is the published BnB
LLM.int8 8-bit bundle and Docker image. It is the least destructive tested
quantization: against the persisted BF16 1024-D reference it has mean cosine
drift 0.003484, maximum drift 0.015382, 100% nearest-neighbor agreement, and
Recall@1/5/10 and nDCG all equal to 1.0. It also passed the 32K capacity probe
and short-input batch scaling while leaving approximately 0.92 GiB between
the vLLM 6.03-GiB startup working set and the physical 8-GiB device before
desktop overhead is considered. The 4-bit NF4 bundle is the memory-saving
alternative, with mean drift 0.0356 and one held-out top-1 miss.

The W2-heavy Thinker layouts are not recommended. RTN W2/W4/W8, GPTQ
W4/W4/W8, and the BF16/W2/W4 boundary all served in at least one constrained
configuration but materially damaged the fixed retrieval screen. The tested
W8 tower experiments do not overturn that conclusion: vision W8 had a
kernel-compatible load path but failed retrieval quality in the mixed GPTQ
composition, while audio W8 was rejected by vLLM's packed-module mapping.
Storage compression alone is therefore not evidence of a useful runtime
candidate.

The requested conservative GPTQ calibration was attempted and measured, but
the all-W4 G128 result failed retrieval fidelity despite successful serving.
AutoRound Light was also attempted with `auto-round 0.15.1`, W4A16,
128 samples, batch 1, sequence length 1024, 50 iterations, low-GPU/CPU-memory
mode, and the `qwen2_5_omni` MLLM template. It loaded all 2,543 tensors, but
its calibration-data subprocess was killed with exit code -9 while casting
2,301 examples and produced no checkpoint. The raw attempt record is
`results/ovis-autoround-light-attempt.json`.

Expensive AutoRound calibration is therefore not justified for the current
release decision: the mature BitsAndBytes path gives much better measured
quality and verified 32K/batch/concurrency results, while both AutoRound and
the conservative GPTQ path failed to provide a quality-preserving release
candidate. Calibration should only be revisited if the remaining VRAM
headroom becomes a hard requirement and a real multimodal calibration set is
available. The 32K and media results are explicitly separated above; no
unsupported fidelity or concurrency claim is inferred for rejected candidates.

## Scope audit and remaining uncertainties

| Requested area | Current evidence | Boundary or uncertainty |
|---|---|---|
| Held-out quality set | 60 deterministic text-text records plus separate image, audio, and video fixtures; multilingual, code, structured, prose, science, medicine, finance, geography, history, software, and math categories are represented | The modality fixture has one item per modality, so it supports drift/output checks, not modality ranking metrics |
| BF16 reference and serving | 660 persisted native-2048 text vectors, persisted 1024-D projection, and three persisted media references; direct vLLM probe recorded a resource-gate failure | No BF16 endpoint latency/throughput because the original checkpoint reached 8,003/8,192 MiB before readiness |
| BnB NF4 and LLM.int8 | Published HF/Docker artifacts, vLLM plugin logs, text fidelity, batch/concurrency, 32K, and media serving/fidelity evidence | NF4 was not promoted to a 32K gate after its earlier 8-GiB long-context failure |
| Thinker W4A16 G128 | 128-sample, sequence-1024 GPTQ calibration and repaired vLLM bundle were measured; AutoRound Light also attempted with 128 samples, batch 1, seq 1024, 50 iterations | GPTQ drift was 1.0052; AutoRound preprocessing was killed with exit code -9 and produced no checkpoint |
| Tower W8A16 | Vision W8 reached packed-kernel load evidence; audio W8 was rejected by vLLM mapping | Vision W8 failed the mixed-composition retrieval screen; no both-tower candidate was justified |
| Thinker W2 sensitivity | Early/interior/boundary W2 placements were screened with quality and load evidence | A finer Q/K/V/O and MLP projection sweep was stopped because broad W2 already failed materially and offered no justified release path |
| Release status | Only quality- and serving-passing BnB 4-bit and 8-bit variants are promoted; rejected artifacts are documented as experimental or unpublished | Results are RTX 3080 Laptop/SM 8.6 specific and should not be generalized to other runtimes without revalidation |
