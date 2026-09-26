FROM vllm/vllm-openai:latest

# LLM Compressor is used only for the offline calibration/packing stage.
# Keep the runtime image separate: vLLM itself already consumes the resulting
# compressed-tensors checkpoint.
RUN python3 -m pip install --no-cache-dir --no-deps \
      llmcompressor==0.14.0 compressed-tensors==0.19.0 \
      datasets==3.6.0 pandas pyarrow multiprocess==0.70.16 \
      xxhash dill==0.3.8 'fsspec[http]==2025.3.0'
