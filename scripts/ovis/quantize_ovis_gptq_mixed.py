"""Attempt a calibration-based GPTQ mixed W2/W4/W8 Ovis checkpoint.

This is intentionally separate from the model-free compressed-tensors RTN
conversion. GPTQ requires a real model forward pass over calibration text.
Only the Thinker language transformer blocks are targeted; multimodal towers,
talker, token2wav, embeddings, and norms stay in their original precision.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from datasets import Dataset
from compressed_tensors.offload.convert.from_accelerate import from_accelerate
from llmcompressor import oneshot
from transformers import AutoProcessor
from transformers.models.qwen2_5_omni.modeling_qwen2_5_omni import (
    Qwen2_5OmniForConditionalGeneration,
)


TEXTS = [
    "A document describes a graph of entities and relations.",
    "Embeddings should preserve semantic similarity across short passages.",
    "The audio and vision towers provide multimodal features for retrieval.",
    "Quantization reduces memory while calibration helps preserve geometry.",
    "A knowledge graph connects people, places, events, and documents.",
    "This is a short calibration sentence for the Ovis embedding model.",
    "Search results should remain stable after weight-only quantization.",
    "The final transformer block uses higher precision in this experiment.",
]

RECIPE_TEMPLATE = """
gptq_mixed_stage:
  obcq_modifiers:
    GPTQModifier:
      block_size: 128
      dampening_frac: 0.01
      actorder: static
      ignore:
        - 're:.*(audio_tower|visual|talker|token2wav).*'
        - 're:.*(embed_tokens|norm).*'
      config_groups:
        W2A16:
          targets:
            - 're:.*(?:thinker\\.)?model\\.layers\\.(?:[0-9]|1[0-3])\\..*'
          weights:
            num_bits: {early_bits}
            type: int
            symmetric: true
            strategy: group
            group_size: 128
        W4A16:
          targets:
            - 're:.*(?:thinker\\.)?model\\.layers\\.(?:1[4-9]|2[0-6])\\..*'
          weights:
            num_bits: {middle_bits}
            type: int
            symmetric: true
            strategy: group
            group_size: 128
        W8A16:
          targets:
            - 're:.*(?:thinker\\.)?model\\.layers\\.27\\..*'
          weights:
            num_bits: {final_bits}
            type: int
            symmetric: true
            strategy: group
            group_size: 128
"""


def make_recipe(early_bits: int, middle_bits: int, final_bits: int) -> str:
    return RECIPE_TEMPLATE.format(
        early_bits=early_bits,
        middle_bits=middle_bits,
        final_bits=final_bits,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--max-seq-length", type=int, default=128)
    parser.add_argument("--early-bits", type=int, default=2)
    parser.add_argument("--middle-bits", type=int, default=4)
    parser.add_argument("--final-bits", type=int, default=8)
    args = parser.parse_args()

    recipe_path = Path(args.recipe)
    recipe_path.write_text(
        make_recipe(args.early_bits, args.middle_bits, args.final_bits),
        encoding="utf-8",
    )
    if args.samples <= 0:
        raise ValueError("--samples must be positive")
    calibration_texts = [
        f"{TEXTS[index % len(TEXTS)]} Calibration example {index} preserves retrieval geometry."
        for index in range(args.samples)
    ]
    dataset = Dataset.from_dict({"text": calibration_texts})
    model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        args.model,
        dtype="bfloat16",
        device_map="auto",
        low_cpu_mem_usage=True,
        offload_folder="/out/Ovis-Omni-Embedding-3B-gptq-offload",
    )
    model.thinker.config._name_or_path = args.model
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)
    # Convert Accelerate's partial-forward disk hooks before GPTQ installs its
    # own quantized forwards. Otherwise compressed-tensors 0.19 cannot wrap a
    # functools.partial created by Accelerate.
    from_accelerate(model)

    oneshot(
        # Ovis' composite wrapper exposes a variadic forward. The Thinker
        # submodule has the concrete causal-LM forward that GPTQ tracing needs.
        model=model.thinker,
        processor=processor,
        dataset=dataset,
        recipe=str(recipe_path),
        output_dir=args.output,
        precision="bfloat16",
        trust_remote_code_model=True,
        num_calibration_samples=args.samples,
        max_seq_length=args.max_seq_length,
        batch_size=1,
        data_collator="truncation",
        sequential_offload_device="cpu",
        pipeline="sequential",
    )


if __name__ == "__main__":
    main()
