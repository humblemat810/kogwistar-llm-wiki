# Ovis Omni Embedding vLLM BitsAndBytes image

This image includes `vllm-bnb-plugin`, which is required for pre-quantized
BitsAndBytes checkpoints. Set `MODEL_ID` or mount a local checkpoint at
`MODEL_PATH`. The default command uses the pooling embedding API:

```powershell
docker run --rm --gpus all -p 8000:8000 `
  -e MODEL_ID=pt810/Ovis-Omni-Embedding-3B-bnb-4bit-vllm `
  profchan/kogwistar-llm-wiki-embedding:vllm-bnb-4bit
```

The published tags and source checkpoints are:

| tag | checkpoint | tested context |
|---|---|---:|
| `vllm-bnb-4bit` | `pt810/Ovis-Omni-Embedding-3B-bnb-4bit-vllm` | 512 tokens |
| `vllm-bnb-8bit` | `pt810/Ovis-Omni-Embedding-3B-bnb-8bit-vllm` | 512 tokens |

Canonical model-qualified tags are:

- `ovis-omni-embedding-3b-vllm-bnb-4bit`
- `ovis-omni-embedding-3b-vllm-bnb-8bit`

The shorter tags remain compatibility aliases.

Both were tested with vLLM HTTP `/v1/embeddings` and returned finite,
normalized 1024-dimensional vectors. The older all-layer multimodal artifact
is not used as the vLLM image payload; the published checkpoints quantize the
Thinker language weights and retain the multimodal towers in BF16.

Published image digests and smoke-test results:

- `vllm-bnb-4bit`: `sha256:18676281e80ac5019b55003aca5aaa7f08ffd50f8f50332d4d9af6b36f5de3e1`; 1024 dimensions, finite, norm `1.0000000051`.
- `vllm-bnb-8bit`: `sha256:fb391acdf8e4dccdb4af597485188119bc5ffe7b545fb0d8d254abc41897999d`; 1024 dimensions, finite, norm `0.9999999945` (latest exact-tag recheck).

Both images were tested with vLLM 0.30.0 and `vllm-bnb-plugin` 0.0.3 on an
8-GiB RTX 3080 Laptop GPU. The tested 512-token context is the supported
published configuration; full 32,768-token BnB serving is not claimed because
the plugin's CPU-offload path failed during the `bnb_quant_state` lookup.
