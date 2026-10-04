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

### Additional screening

Nomic `nomic-embed-text-v2-moe` completed a CPU smoke point at 128 words with
batch size 2, no warmup, one repetition: **1.78 items/s**, 768 dimensions,
512-token maximum, and approximately 2.55 GiB resident memory. This is not a
matched row and is intentionally labelled a screening result. Its custom
remote code also warned that Nomic's Megablocks fork was not installed.

GTE multilingual base, BGE-M3, and Qwen3-Embedding-0.6B were attempted in the
CUDA container, but the bounded run remained in checkpoint/custom-code loading
and did not produce a steady-state row. They are therefore **not ranked** by
this report. Their incomplete attempts are useful operational data: for a
fair comparison, download and cache each revision before starting the timed
matrix, then report load time separately from encode throughput.

Raw artifacts from the completed runs are kept under the ignored
`test-results/` directory, including `text-embeddings-cpu-small.json`,
`text-embeddings-nomic-check.json`, `text-embeddings-cuda-minilm.json`,
`text-embeddings-cuda-e5.json`, and `text-embeddings-cuda-para.json`.

These numbers are throughput measurements, not retrieval-quality scores. A
model should not be selected from speed alone; the next comparison should use
the same labelled multilingual retrieval set and report Recall@k/MRR or nDCG
alongside these device-specific measurements.
