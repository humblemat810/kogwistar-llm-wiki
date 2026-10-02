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

Start the server from PowerShell after verifying both files exist. The helper
discovers the WSL virtual-network address used by Docker and binds only to that
interface, rather than exposing the unauthenticated llama.cpp endpoint on every
host interface:

```powershell
./scripts/start_local_bonsai.ps1 -Context 8192 -ReasoningEffort medium -ReasoningBudget 2048
```

In the current Windows/Docker Desktop setup, the WSL interface is
`192.168.64.1`, and the maintenance container reached that address in a TCP
probe. The `.env` value `host.docker.internal` resolved to a different Docker
gateway address and was not the verified route for this host. Set
`KOGWISTAR_MAINTENANCE_BASE_URL` to `http://<WSL-IPv4>:8181/v1` for the active
Compose process; keep this host-specific override out of the committed `.env`
and rediscover it after Docker/WSL network changes. Do not bind to `0.0.0.0`:
the llama.cpp endpoint has no authentication configured. Context `8192` is
verified with this exact local binary/model configuration: maintenance prompts
of 3,167 tokens and 1,024 generated tokens completed without truncation. This
proves an operational context size, not the model's maximum.

The launch helper also sets llama.cpp's server-side reasoning effort and token
budget. Those options affect only this local llama.cpp server. They are not
passed through the application provider abstraction, so changing the configured
maintenance provider to Codex, Anthropic, or another OpenAI-compatible endpoint
does not make those providers receive llama.cpp-specific request fields. The
2,048-token setting is a conservative trial value, not a proven maximum for
every maintenance response.

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

Durable parser requests may set `parse_limits.wall_time_seconds` (maximum
3,600 seconds) and an explicit `parse_limits.parser_profile`. The profile is
combined with the validated parse-limit fingerprint to form the derivation
identity. This permits a bounded retry of the same immutable source revision
with a larger wall-time budget without editing the existing session or
generation. For example, a 900-second session and a 2,400-second retry are
separate derivations even when they use the same named profile and bytes. The
maintenance request identity also includes parse limits, so the retry is queued
as a distinct job. Keep the original source URI and exact bytes; verify the new
revision ID/digest is unchanged and the new session ID/generation ID differs.
Only treat the retry as successful when its frontier is empty, the ParseView is
active, and `parsed_graph_persisted` readiness is recorded. A model HTTP 200 or
an exhausted wall budget is not parse success.

All maintenance strategy exception paths use the same terminal-budget guard.
An explicit wall-time or maintenance-budget exhaustion is marked `FAILED` with
the active claim token and is not requeued; transient provider/network
timeouts remain retryable. This prevents a timed-out durable job from renewing
its lease indefinitely while preserving recovery for temporary provider
failures.

For a deliberately scaled local deployment, set
`KOGWISTAR_MAINTENANCE_MULTI_WORKER=true`. Each maintenance replica then uses
an instance-specific control socket under the shared data directory, while
the durable queue, leases, and workspace budgets remain the coordination
authority. The default remains one worker with `maintenance.sock` for
backward-compatible local operation. The released image must include the
socket-isolation change before scaling replicas in Compose.

The same switch also gives the parser's import-time SQLite ingest log an
instance-specific path (`document_ingest-<container-hostname>.sqlite`). This
avoids two replicas racing to initialize one local parser database. Kogwistar's
PostgreSQL backend also serializes the database-wide pgvector extension
creation with a transaction-scoped advisory lock; `CREATE EXTENSION IF NOT
EXISTS` alone is not sufficient when fresh replicas initialize concurrently.
The maintenance Compose healthcheck verifies the default socket or the
replica's hostname-specific socket without using an invalid wildcard test.

Local verification on 2026-10-02 used a fresh isolated Compose project with
two maintenance replicas. Both replicas stayed running with zero restarts and
became healthy; each created a distinct parser SQLite file and control socket.
No `database is locked` or duplicate `pg_extension` error occurred. This is a
local runtime verification of the hardening branch, not a claim that the
corresponding GitHub PR checks are green.

If the configured provider reports a context-window/input-token overflow during
an observation or parser call, the assessment/job is marked `blocked_context`,
the current maintenance plan is terminated without retrying that job, and
durable maintenance control sets both `request_enabled=false` and
`background_enabled=false` with `status_reason=blocked_context_window`. This
aborts the maintenance experiment as a whole, including direct/follow-up work;
the daemon does not automatically switch providers, retry the oversized job,
or continue with other queued jobs. After a hardware/model upgrade or deliberate
context reduction, inspect the control state and explicitly re-enable the modes
you want to resume:

```powershell
python -m kogwistar_llm_wiki daemon maintenance-control --status
python -m kogwistar_llm_wiki daemon maintenance-control --request-enabled true
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

For this finance-text experiment, prefer the optional CPU-only BGE-small profile
over CLIP when indexing text-only source units. The pinned model is
`BAAI/bge-small-en-v1.5` at revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`; its model card lists MIT licensing,
384-D embeddings, and a 512-token sequence limit. The model author reports
retrieval results for the model, but those are not a finance-domain evaluation
or a benchmark on this host. BGE is English and text-only: it must reject image
inputs. Use CLIP when a shared text/image space is needed, or Qwen3-VL for the
existing multimodal embedding profile.

Download the exact BGE snapshot outside the repository, for example:

```powershell
huggingface-cli download BAAI/bge-small-en-v1.5 `
  --revision 5c38ec7c405ec4b44b94cc5a9bb96e735b38267a `
  --local-dir D:/models/bge-small-en-v1.5
```

Run its optional standalone CPU service independently:

```powershell
docker compose -f compose.embedding-bge-cpu.yml up -d --build
```

It binds only to host loopback port `8793`, defaults to one CPU and 2 GiB, and
uses a read-only model bind mount. The service applies BGE's retrieval query
instruction to query inputs and not document inputs. Its profile fingerprint
includes the pinned model revision, CLS pooling, normalization, instruction,
and sequence limit. This 384-D space is distinct from the current Qwen/CLIP
profiles; do not connect it to an existing graph or index unless that graph was
created for the exact same profile. This overlay starts only the embedding
service; wiring or migrating the active graph requires an explicit profile
change and separate validation.

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
