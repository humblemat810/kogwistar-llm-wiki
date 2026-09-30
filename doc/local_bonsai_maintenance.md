# Local Bonsai Maintenance Profile

This profile runs maintenance against a host-side llama.cpp server, while
PostgreSQL and the LLM-Wiki services remain in Compose. It does not use Ollama
and it does not put the Bonsai model or its vision projector in the container.

## Host Model Server

The custom build is expected at `D:\prism-llama.cpp`. The model files must be
present before starting the worker:

  ```text
D:\models\bonsai2\Ternary-Bonsai-2-27B-PTQ1_0.gguf
D:\models\bonsai2\Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf
  ```

The second file is the Bonsai **multimodal projection** (`mmproj`) for its
vision encoder. It is required when the local model receives image input and
must be kept with the matching language GGUF. It is not the LLM-Wiki vector
embedding encoder: maintenance chat generation and vector indexing remain
separate model/profile responsibilities. Do not point
`LLM_WIKI_EMBEDDING_MODEL` at this file.

Start the server from PowerShell after verifying both files exist:

```powershell
& 'D:\prism-llama.cpp\build\bin\Release\llama-server.exe' `
  -m 'D:\models\bonsai2\Ternary-Bonsai-2-27B-PTQ1_0.gguf' `
  --mmproj 'D:\models\bonsai2\Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf' `
  -c 8192 `
  -ngl 999 `
  --parallel 1 `
  --cache-type-k q4_0 `
  --cache-type-v q4_0 `
  -fa on `
  --fit-target 800 `
  --host 0.0.0.0 `
  --port 8181 `
  -lv 4
```

The container reaches the host through `host.docker.internal`. Binding
`0.0.0.0` is needed for Docker Desktop; restrict Windows Firewall to local
Docker/WSL traffic if the host is not otherwise trusted. Context `8192` is
verified with this exact local binary/model configuration: maintenance prompts
of 3,167 tokens and 1,024 generated tokens completed without truncation. This
proves an operational context size, not the model's maximum.

The local GGUF declares `qwen35.context_length=262144`; the upstream model card
describes the same inherited model limit and an architecture with roughly 75%
linear attention. Its quickstart uses 32K, while the current known-issues guide
suggests 64K for long reasoning/output exhaustion. Neither is a measurement of
VRAM fit for this host. The host GPU is an 8 GiB RTX 3080 Laptop GPU. Therefore
`8192` remains the conservative verified operating setting. The empirical
maximum for this machine is unknown; do not raise it based on the model's
advertised limit or generic quickstart. Establishing a maximum requires a
controlled ladder (8K, 12K/16K, then 24K/32K only if memory headroom allows),
with cold-start load, long prompt prefill, full output generation, peak VRAM/RSS,
and repeatability checks at each step. That benchmark was not run after the
authorized ten-hour monitoring window closed.

An offline estimate from this exact GGUF header gives a useful ladder, not a
fit guarantee: 64 blocks with `full_attention_interval=4` imply 16 full-attention
layers; four KV heads and 256-wide key/value vectors at Q4_0 (18 bytes per 32
values) cost about 18 KiB per context token. That is approximately 144 MiB at
8K, 288 MiB at 16K, 576 MiB at 32K, 1.125 GiB at 64K, and 4.5 GiB at 262K for
attention KV alone. This excludes recurrent state, CUDA/runtime workspaces,
projector residency, allocator overhead, and other GPU users. It supports
trying 16K and then 32K only in a newly authorized controlled run with observed
headroom; it does not establish that either fits or that 32K is the maximum.

The Bonsai `--mmproj` file is its image-input projector. It is not an LLM-Wiki
embedding encoder. The configured Ovis/Qwen embedding model has its own vision
encoder and multimodal projection parameters in its Hugging Face checkpoint;
it does not use Bonsai's GGUF `mmproj`. Keep those model revisions and caches
separate. The current production embedding endpoint is not running in this
minimal profile, and PostgreSQL currently has a `vector(2)` demo layout, so do
not treat this graph as semantically embedded or write a different vector
dimension into it.

## Model Cache And Backup Locations

For this Windows account, the default Hugging Face Hub cache root is
`C:\Users\chanh\.cache\huggingface`. It is a cache, not the authoritative
runtime location for every model used here. The Bonsai GGUF and matching
`mmproj` used by llama.cpp are under `D:\models\bonsai2`. The pinned CLIP
checkpoint used by the optional CPU encoder is under
`D:\models\sentence-transformers-clip-vit-b32-327ab672` and is now also cached
at the default C-drive Hub location under
`models--sentence-transformers--clip-ViT-B-32`, revision
`327ab6726d33c0e22f920c83f2ff9e4bd38ca37f`.

CLIP contains learned text and image projection heads in its own checkpoint;
both map to its shared 512-D space. It does not need a separate llama.cpp-style
`mmproj` file. The checkpoint SHA-256 is pinned and checked by the encoder.
This CLIP projection is separate from Bonsai's vision `mmproj` and cannot be
substituted for it.

Back up or re-download the actual model files at those D-drive paths separately
from the redacted environment backup. If the Hugging Face cache root is changed
with `HF_HOME` or `HF_HUB_CACHE`, record that override with the local operator
configuration; do not assume Compose mounts the cache automatically.

## Maintenance Settings

The local `.env` uses the OpenAI-compatible adapter pointed at llama.cpp:

```dotenv
KOGWISTAR_MAINTENANCE_PROVIDER=openai
KOGWISTAR_MAINTENANCE_PROVIDER_CHAIN=openai
KOGWISTAR_MAINTENANCE_MODEL=Ternary-Bonsai-2-27B-PTQ1_0
KOGWISTAR_MAINTENANCE_BASE_URL=http://host.docker.internal:8181/v1
KOGWISTAR_MAINTENANCE_API_KEY_ENV=LLM_WIKI_LOCAL_MODEL_API_KEY
LLM_WIKI_LOCAL_MODEL_API_KEY=local-bonsai
LLM_WIKI_MAINTENANCE_BACKGROUND_MAX_LLM_CALLS=2
LLM_WIKI_MAINTENANCE_BACKGROUND_MAX_TOKENS=16000
LLM_WIKI_MAINTENANCE_BACKGROUND_MAX_STEPS=6
LLM_WIKI_MAINTENANCE_BACKGROUND_MAX_TIME_SECONDS=300
```

The background scheduler now selects one workspace-scoped graph candidate per
interval and enqueues a bounded `review_maintenance_subject` job. That job uses
the same configured Bonsai-compatible provider to review source grounding,
relations, parent context, and neighboring concepts. The response is read-only:
findings must cite identifiers present in the bounded frame, and only the host
can translate supported findings into existing revision-fenced follow-ups.
Background jobs allow at most one follow-up round and carry explicit call,
token, step, time, and observation-frame budgets. Usage is recorded through the
existing profile budget sink. A deterministic `distill` completion is not
evidence of a Bonsai call; look for `maintenance_observation_provider_call`
with `provider=openai`, the Bonsai model name, and `llm_call_count=1`, alongside
the corresponding llama-server request log.

The current LLM-Wiki observation critic uses the shared provider factory and
does not itself set provider-specific reasoning or completion-token limits.
Earlier experiment notes about `reasoning_effort=medium`, a 2,048-token
reasoning budget, or a 4,096-token completion cap must not be read as proof
that those limits are present in this current call path. The configured model
may reject an oversized request; recognized context-window failures are now
made terminal and pause future background cycles rather than silently falling
back to another provider. A provider-neutral generation cap still requires a
verified adapter contract for every supported backend.

The scheduler also carries explicit limits into each job. This avoids the
previous silent no-op behavior where an omitted `max_llm_calls` became zero.
Without overrides, the scheduler defaults to 4 steps and 180 seconds; the
dedicated Bonsai profile sets 6 steps and 300 seconds. A scheduler regression
test verifies that these explicit profile values reach the queued observation
job rather than being replaced with the defaults.
Direct parse requests retain their explicit budgets and their configured
follow-up round limit.

If the configured provider reports a context-window/input-token overflow during
an observation or parser call, the assessment/job is marked `blocked_context`,
the current maintenance plan is terminated without retrying that job, and
durable maintenance control sets `background_enabled=false` with
`status_reason=blocked_context_window`. The daemon does not automatically switch
providers or retry the same oversized job. Explicit request maintenance remains
enabled. After changing hardware or deliberately reducing the frame/context
requirements, inspect the control state and explicitly resume background work:

```powershell
python -m kogwistar_llm_wiki daemon maintenance-control --status
python -m kogwistar_llm_wiki daemon maintenance-control --background-enabled true
```

Context-overflow classification recognizes common OpenAI-compatible, Anthropic-
style, Google, and llama.cpp error wording; it is a fail-safe heuristic, not a
preflight guarantee that a provider's advertised context window will fit a
specific prompt. The current LLM-Wiki pin does not yet include the separate
parser feature branch's optional Anthropic adapter. A profile switch rebuilds
the default critic with the newly selected supported provider; injected custom
critics are intentionally left untouched.

After changing provider settings, recreate only the maintenance service:

```powershell
docker compose -f compose.yml -f compose.memory-agent.yml up -d --no-deps --force-recreate maintenance
```

The Compose data volumes are not removed by this command. Confirm the worker
is healthy and inspect logs for `call_budget` greater than zero before judging
the run. A successful workflow with `call_used=0` is not a model-backed run.

## CPU Embeddings

The standalone service now supports the existing Qwen3-VL encoder and an
optional CLIP ViT-B/32 CPU encoder. The CLIP checkpoint is pinned to revision
`327ab6726d33c0e22f920c83f2ff9e4bd38ca37f`; only its `model.safetensors` and
processor/tokenizer files are available both at
`D:\models\sentence-transformers-clip-vit-b32-327ab672` and in the default
Hugging Face cache at
`C:\Users\chanh\.cache\huggingface\hub\models--sentence-transformers--clip-ViT-B-32`
(revision snapshot `327ab6726d33c0e22f920c83f2ff9e4bd38ca37f`). The weights are
about 605 MB. Their SHA-256 is
`99d28a652e6ec46629ab7047a0ac82c69b1fe11e0ce672c43af65d3a9a3fc05d`, verified
locally and checked by the encoder on startup when loading this pinned local
copy. The
encoder loads the checkpoint's learned text and vision projection layers into
one shared 512-D space. Unlike Bonsai's `--mmproj`, CLIP's modality projections
are weights inside `model.safetensors`, not a separate projector file. Items
with both text and image average their normalized projected vectors and
renormalize the result.

### Smaller Candidate: Apple MobileCLIP-S0

Apple's [MobileCLIP-S0 checkpoint](https://huggingface.co/apple/MobileCLIP-S0)
is about 216 MB, versus about 605 MB for the current ViT-B/32 checkpoint.
Apple reports the model as its smallest image-text model and gives benchmark
comparisons in its [official implementation](https://github.com/apple-aiml-research/ml-mobileclip).
This is a promising lower-weight CPU candidate, not a host benchmark: those
published speed figures do not establish its latency or peak RSS on this
Windows machine.

Do not replace the current encoder solely on file size. MobileCLIP-S0 uses an
Apple-specific model license and a pickle-based `.pt` checkpoint, and the
official inference path uses Apple's `mobileclip` implementation or a patched
OpenCLIP installation. It is not compatible with the current Hugging Face
Transformers CLIP loader. Before adopting it, review the model terms and
checkpoint supply-chain implications, pin the exact revision and digest, then
add a dedicated adapter plus actual text/image projection, RSS, latency, and
retrieval-quality tests. The CLIP ViT-B/32 checkpoint has now passed a bounded
real CPU-device inference smoke for text, image, and mixed input using the
installed CUDA-enabled Torch build and also passed inference in the isolated
CPU-only image plus the in-process FastAPI route contract. Peak RSS, a live
Uvicorn/Compose service, and retrieval quality remain unverified.
Until MobileCLIP is evaluated, CLIP ViT-B/32 remains the implemented optional
encoder candidate.

Run the optional CPU-only, localhost-bound smoke service independently of the
main Compose graph:

```powershell
docker compose -f compose.embedding-clip-cpu.yml up -d --build
python scripts/smoke_clip_cpu.py
```

The smoke sends finance-related text plus a synthetic image and validates
profile identity, ordering, vector dimensions, and normalization. To reuse the
download at another location, set `LLM_WIKI_CLIP_MODEL_HOST_PATH`. This overlay
does not connect the encoder to REST/MCP and does not write embeddings to
Postgres. CLIP is a compact general image/text model, not a finance-specialized
text retriever; keep BM25 authoritative for exact tickers, dates, and figures,
and evaluate semantic retrieval against a separate text profile before use.

The pinned model also passed a real CPU projection smoke through the FastAPI
app in-process: readiness and text/image/mixed embedding requests returned
HTTP 200 with the pinned profile and normalized 512-D vectors. This does not
mean the optional Docker service is currently running; it remains a separate
profile and has not been attached to the active graph.

Do not change the current database embedding dimension in place. The active
demo database has a `vector(2)` layout. Create a new profile-isolated graph (or
perform a reviewed migration) before writing 512-D vectors. Model and
preprocessing fingerprints, not dimension alone, define a compatible vector
space.

## Verification Order

1. Verify both GGUF files (language model plus matching `mmproj`) and query
     `http://localhost:8181/v1/models`.
2. Recreate only `maintenance` and confirm it resolves to `openai` and the
   Bonsai endpoint.
3. Run one bounded direct maintenance request for the existing stock sources.
4. Confirm the bridge has no traffic; Bonsai traffic should appear in the
   llama-server log instead.
5. Confirm `call_used >= 1`, a completed maintenance job, and evidence-backed
   graph changes before adding documents.
6. Let background maintenance run on its interval and review parsing,
   cross-links, duplicates, and unsupported claims before expanding the corpus.

For direct MCP `maintain` requests, pass the logical source ID returned by the
source/status tools as `source_document_ids`. The currently active immutable
revision-document ID is accepted as an alias when a caller only has that ID.
Historical/stale revision IDs are rejected rather than silently redirected to
the current source revision. A successful queue response proves only that the
request was accepted; verify the resulting job status and reviewed graph changes
separately.

The configuration backup must never include API tokens or database passwords.
Keep the secret-bearing `.env` local and back up this document plus a redacted
effective environment separately.
