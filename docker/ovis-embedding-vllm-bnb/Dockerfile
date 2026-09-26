FROM vllm/vllm-openai:latest

# vLLM's BitsAndBytes backend is an out-of-tree plugin. Keeping it in the
# image makes the published serving image reproduce the tested loader path.
RUN pip install --no-cache-dir vllm-bnb-plugin

ARG MODEL_ID=pt810/Ovis-Omni-Embedding-3B-bnb-4bit-vllm
ENV MODEL_ID=${MODEL_ID}
ENV VLLM_RUNNER=pooling
ENV VLLM_CONVERT=embed
ENV VLLM_MAX_MODEL_LEN=512
ENV VLLM_GPU_MEMORY_UTILIZATION=0.80

ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["vllm serve ${MODEL_PATH:-$MODEL_ID} --runner ${VLLM_RUNNER} --convert ${VLLM_CONVERT} --pooler-config '{\"dimensions\":1024}' --max-model-len ${VLLM_MAX_MODEL_LEN} --gpu-memory-utilization ${VLLM_GPU_MEMORY_UTILIZATION}"]
