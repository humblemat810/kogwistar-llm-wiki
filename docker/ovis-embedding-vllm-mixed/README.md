# Mixed compressed-tensors Ovis embedding image

This is a runtime-only vLLM image. The default build serves
`pt810/Ovis-Omni-Embedding-3B-mixed-w2-w4-w8-ct` with vLLM's
compressed-tensors backend and a 1024-dimensional embedding pooler.

The same Dockerfile was also built with `MODEL_ID` overrides for the GPTQ
experiments:

- `vllm-gptq-w4-w4-w8`: vLLM-tested repaired GPTQ fallback.
- `vllm-gptq-w2-w4-w8-experimental`: published for reproducibility, but W2
  packed weights are rejected by vLLM 0.30.0's Marlin loader.
- `vllm-high-low-high-w8-w2-w8-experimental`: W8 early/final and W2 middle
  RTN layout; published for reproducibility, but exceeds the current 8-GiB
  GPU during startup.
- `vllm-gptq-w4-w4-w8-fullcontext`: verified 32,768-token configuration using
  FP8 KV cache and 2 GiB CPU offload; defaults to 1024-dimensional output.

Mount a local checkpoint with `-e MODEL_PATH=/model -v <checkpoint>:/model:ro`
to use a local model. The default pooler requests 1024 dimensions.

Canonical model-qualified tags for this repository are prefixed with
`ovis-omni-embedding-3b-`, for example
`ovis-omni-embedding-3b-vllm-boundary-bf16-w2-w4-fullcontext`. The older
scheme-only tags remain compatibility aliases; new images should use the
model-qualified form.

The boundary mixed-precision full-context tag is
`profchan/kogwistar-llm-wiki-embedding:vllm-boundary-bf16-w2-w4-fullcontext`.
It uses BF16 for layers 0-1, W2A16 for layers 2-21, and W4A16 for layers
22-27. The corresponding HF checkpoint is
`pt810/Ovis-Omni-Embedding-3B-boundary-bf16-w2-w4`.

Exact-image validation used a 32,768-token limit, FP8 KV cache, and 2 GiB CPU
offload. A 32,701-token request returned 1024 finite dimensions with norm
1.0000000662 in 12.27 seconds. Image digest:
`sha256:477f88f2c1c0fe7e5a55ec0f39ee3a6fe2194fb1145f3bf72aab2db3e3ca05e3`.
