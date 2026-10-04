# Text Embedding Benchmark

This benchmark compares CPU and CUDA text-only embedding throughput. It is
separate from the multimodal Qwen/Ovis experiment because text encoders have
different model families, pooling contracts, and context limits.

## Models

| Key | Hugging Face model | Intended role |
| --- | --- | --- |
| `all-minilm-l6-v2` | `sentence-transformers/all-MiniLM-L6-v2` | Small English baseline |
| `multilingual-e5-small` | `intfloat/multilingual-e5-small` | Fast multilingual baseline |
| `paraphrase-multilingual-minilm-l12-v2` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Simple multilingual baseline |
| `gte-multilingual-base` | `Alibaba-NLP/gte-multilingual-base` | Quality/long-context multilingual baseline |
| `nomic-embed-text-v2-moe` | `nomic-ai/nomic-embed-text-v2-moe` | Newer efficiency/quality candidate |
| `bge-m3` | `BAAI/bge-m3` | Strong multilingual and hybrid-retrieval candidate |
| `qwen3-embedding-0.6b` | `Qwen/Qwen3-Embedding-0.6B` | Modern long-context candidate |

The rough context and dimension values should not be treated as benchmark
results. The runner reads each loaded model's actual tokenizer limit and vector
dimension, and records the effective token count after truncation.

## Method

The runner is [`scripts/benchmark_text_embedding_models.py`](../scripts/benchmark_text_embedding_models.py).
It uses one deterministic text corpus shape at 128, 512, 2048, and 4096
repeated words, batch size 8, one warmup, and two timed repetitions. It reports
median batch latency, items per second, requested/effective tokens, output
dimension, resident memory, and GPU allocation. Models are loaded sequentially
so the comparison does not require every checkpoint to fit in memory together.

Install the optional benchmark dependency in an isolated environment:

```text
python -m pip install "sentence-transformers<6" "transformers==4.57.1" einops psutil
```

Run CPU:

```text
python scripts/benchmark_text_embedding_models.py \
  --device cpu --models all --lengths 128 512 2048 4096 \
  --batch-size 8 --warmups 1 --repeats 2 \
  --trust-remote-code \
  --output test-results/text-embeddings-cpu.json
```

Run CUDA in a CUDA-enabled environment:

```text
python scripts/benchmark_text_embedding_models.py \
  --device cuda --models all --lengths 128 512 2048 4096 \
  --batch-size 8 --warmups 1 --repeats 2 \
  --trust-remote-code \
  --output test-results/text-embeddings-cuda.json
```

For exact tokenizer-targeted runs, use `--token-lengths` instead of
`--lengths`. For example, the long-context comparison uses:

```text
python scripts/benchmark_text_embedding_models.py \
  --device cuda \
  --models gte-multilingual-base bge-m3 qwen3-embedding-0.6b \
  --token-lengths 2048 8192 \
  --batch-size 1 --warmups 1 --repeats 1 --trust-remote-code \
  --output test-results/text-embeddings-cuda-long-context.json
```

The result records both the requested target and the actual tokenizer count.
The 8192-token case is valid for GTE and BGE-M3; Qwen3-Embedding supports it
and can go beyond it. A model whose maximum is below the target must not be
silently treated as a long-context result.

The comparison pins each model to a recorded Hugging Face revision. Nomic and
Qwen require custom model code; `--trust-remote-code` is therefore explicit
and should only be used for the pinned revisions shown in the benchmark JSON.

## Interpretation Rules

- A model's throughput is comparable only within the same device, batch size,
  warmup policy, and model input shape.
- `requested_words` is not a tokenizer count. Use `effective_tokens` to see
  how much input the model actually received after its own truncation limit.
- A short-context model reaching the same throughput at 4096 words may simply
  be re-encoding the truncated prefix; it is not evidence of 4096-token support.
- Throughput is not retrieval quality. A separate labeled retrieval set is
  required to choose a default model.
- Model vectors belong to separate embedding profiles. Equal dimensions do not
  make spaces interchangeable, and changing the model requires re-indexing.

## Results

The first real screening run used the same text fixtures on Windows CPU and
an RTX 3080 CUDA container. The matched rows used batch size 4 on CPU and
batch size 8 on CUDA, one warmup, and one or two timed repetitions. Because
the batch sizes differ, compare CPU with CPU and CUDA with CUDA; do not divide
one column by the other as a device-independent score.

### Matched baselines

Items per second, after warmup:

| Model | Device | 128 words | 512 words | Effective tokens at 128 / 512 | Dimension | Max tokens |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| all-MiniLM-L6-v2 | CPU | 35.7 | 22.9 | 157 / 256 | 384 | 256 |
| multilingual-e5-small | CPU | 18.6 | 5.6 | 193 / 512 | 384 | 512 |
| paraphrase-multilingual-MiniLM-L12-v2 | CPU | 22.3 | 25.0 | 128 / 128 | 384 | 128 |
| all-MiniLM-L6-v2 | CUDA | 259.8 | 240.7 | 157 / 256 | 384 | 256 |
| multilingual-e5-small | CUDA | 137.1 | 120.9 | 193 / 512 | 384 | 512 |
| paraphrase-multilingual-MiniLM-L12-v2 | CUDA | 164.0 | 139.6 | 128 / 128 | 384 | 128 |

The paraphrase model's 512-word row is not a 512-token measurement: it
truncates to 128 tokens. MiniLM also truncates the longer fixture to 256
tokens. E5 is the only matched baseline here that actually processes the
full 512-token effective input.

The complete short-context artifacts also preserve latency, memory, and cold
load details. CPU used batch size 4; CUDA used batch size 8. Each run used one
warmup; CPU used one timed repetition and CUDA used two.

| Model | Device | Load seconds | 128-word median | 512-word median | Resident memory | GPU memory |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| all-MiniLM-L6-v2 | CPU | 26.54 | 111.93 ms | 174.94 ms | 524 / 545 MiB | 0 MiB |
| multilingual-e5-small | CPU | 7.66 | 214.61 ms | 720.34 ms | 823 / 831 MiB | 0 MiB |
| paraphrase-multilingual-MiniLM-L12-v2 | CPU | 5.51 | 179.61 ms | 160.20 ms | 832 / 834 MiB | 0 MiB |
| all-MiniLM-L6-v2 | CUDA | 14.71 | 30.79 ms | 33.23 ms | 1209 / 1213 MiB | 94.78 MiB |
| multilingual-e5-small | CUDA | 22.52 | 58.34 ms | 66.16 ms | 1573 / 1577 MiB | 456.95 MiB |
| paraphrase-multilingual-MiniLM-L12-v2 | CUDA | 20.21 | 48.79 ms | 57.33 ms | 1618 / 1623 MiB | 456.95 MiB |

These rows are the previously completed short-context runs, not estimates.
Their raw JSON artifacts are `text-embeddings-cpu-small.json`,
`text-embeddings-cuda-minilm.json`, `text-embeddings-cuda-e5.json`, and
`text-embeddings-cuda-para.json` under the ignored `test-results/` directory.

The long-context candidates were also measured at the same short word counts
on CPU, using batch size 1. These rows are intentionally kept separate from
the matched baseline table because the batch size and runtime footprint differ.

| Model | 128-word items/s | 512-word items/s | Effective tokens | Dimension | Max tokens | Resident memory at 128 / 512 |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| nomic-embed-text-v2-moe | 2.186 | 1.124 | 193 / 512 | 768 | 512 | 3277 / 2573 MiB |
| gte-multilingual-base | 3.003 | 0.771 | 193 / 764 | 768 | 8192 | 4023 / 4048 MiB |
| bge-m3 | 1.284 | 0.323 | 193 / 764 | 1024 | 8192 | 5445 / 5472 MiB |
| qwen3-embedding-0.6b | 0.912 | 0.255 | 134 / 529 | 1024 | 32768 | 7828 / 7941 MiB |

The corresponding raw artifact is
`text-embeddings-cpu-long-short-context.json`. The short rows use repeated
words rather than exact tokenizer targets; use the exact long-context table
below when comparing 2048 and 8192-token inputs.

### Additional screening

Nomic `nomic-embed-text-v2-moe` completed a CPU smoke point at 128 words with
batch size 2, no warmup, one repetition: **1.78 items/s**, 768 dimensions,
512-token maximum, and approximately 2.55 GiB resident memory. This is not a
matched row and is intentionally labelled a screening result. Its custom
remote code also warned that Nomic's Megablocks fork was not installed.

### Exact long-context CPU run

The following rows use exact tokenizer targets, batch size 1, one warmup, and
one timed repetition. The `target_tokens` value is the requested target; the
actual tokenizer count is reported separately because deterministic word
search can land just below the target.

| Model | Device | Target tokens | Actual tokens | Median latency | Items/s | Dimension | Max tokens | Resident memory |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gte-multilingual-base | CPU | 2048 | 2047 | 3616.55 ms | 0.277 | 768 | 8192 | 2068 MiB |
| gte-multilingual-base | CPU | 8192 | 8190 | 21091.92 ms | 0.047 | 768 | 8192 | 2311 MiB |
| bge-m3 | CPU | 2048 | 2047 | 7344.69 ms | 0.136 | 1024 | 8192 | 2053 MiB |
| bge-m3 | CPU | 8192 | 8190 | 58733.51 ms | 0.017 | 1024 | 8192 | 2780 MiB |
| qwen3-embedding-0.6b | CPU | 2048 | 2048 | 15869.68 ms | 0.063 | 1024 | 32768 | 3225 MiB |
| qwen3-embedding-0.6b | CPU | 8192 | 8192 | 78603.21 ms | 0.013 | 1024 | 32768 | 5515 MiB |

Model load times were separate from the timed rows: GTE 77.06 seconds, BGE-M3
162.18 seconds, and Qwen3-Embedding 481.11 seconds on the Windows CPU
environment. These are cold-load timings and should not be confused with
steady-state embedding latency.

### Exact long-context CUDA run

The same exact-token matrix was attempted in the RTX 3080 Laptop CUDA
container. GTE did not reach a stable CUDA encode row; BGE-M3 remained in
checkpoint/custom-code materialization during the bounded run; and Qwen3-
Embedding eventually occupied approximately 7.95 GiB of the 8 GiB device but
did not emit a completed 2k/8k result before the run was stopped. No GPU
throughput number is reported for these three models. This avoids comparing a
partial GPU setup or a non-steady-state load phase with the completed CPU
measurements. A follow-up GPU run should pre-stage complete caches and run one
model per container, then measure 2048 and 8192 separately.

Raw artifacts from the completed runs are kept under the ignored
`test-results/` directory, including `text-embeddings-cpu-small.json`,
`text-embeddings-nomic-check.json`, `text-embeddings-cuda-minilm.json`,
`text-embeddings-cuda-e5.json`, `text-embeddings-cuda-para.json`,
`text-embeddings-cpu-long-short-context.json`,
`text-embeddings-cpu-gte-long.json`, and
`text-embeddings-cpu-bge-qwen-long.json`.

These numbers are throughput measurements, not retrieval-quality scores. A
model should not be selected from speed alone; the next comparison should use
the same labelled multilingual retrieval set and report Recall@k/MRR or nDCG
alongside these device-specific measurements.
