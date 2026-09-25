"""Create a reloadable BitsAndBytes quantized Ovis-Omni checkpoint.

This keeps the original Transformers architecture/configuration and stores the
BitsAndBytes quantization metadata alongside the model.  It is intentionally
separate from GGUF conversion: vLLM/Transformers can consume this format, while
llama.cpp requires a separate GGUF converter.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import torch
from transformers import BitsAndBytesConfig, Qwen2_5OmniForConditionalGeneration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--bits", type=int, choices=(4, 8), required=True)
    parser.add_argument("--dimension", type=int, default=1024)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.source.is_dir():
        raise SystemExit(f"source directory does not exist: {args.source}")
    if args.destination.exists() and any(args.destination.iterdir()):
        raise SystemExit(f"destination is not empty: {args.destination}")
    args.destination.mkdir(parents=True, exist_ok=True)

    if args.bits == 8:
        quantization_config = BitsAndBytesConfig(
            load_in_8bit=True,
            llm_int8_skip_modules=["thinker.audio_tower", "thinker.visual", "talker", "token2wav"],
            llm_int8_enable_fp32_cpu_offload=True,
        )
    else:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
            llm_int8_skip_modules=["thinker.audio_tower", "thinker.visual", "talker", "token2wav"],
            llm_int8_enable_fp32_cpu_offload=True,
        )

    model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        args.source,
        quantization_config=quantization_config,
        device_map="auto",
        max_memory={0: "7GiB", "cpu": "18GiB"},
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
    )
    model.save_pretrained(args.destination, safe_serialization=True, max_shard_size="4GB")

    # Processor/tokenizer files are not model weights and are not always copied
    # by save_pretrained for this multimodal wrapper.
    for item in args.source.iterdir():
        if item.is_file() and item.name not in {
            "config.json",
            "generation_config.json",
            "model.safetensors.index.json",
        }:
            target = args.destination / item.name
            if not target.exists():
                shutil.copy2(item, target)

    metadata = {
        "source": str(args.source),
        "quantization": "bitsandbytes",
        "bits": args.bits,
        "bnb_4bit_quant_type": "nf4" if args.bits == 4 else None,
        "modules_kept_in_original_dtype": ["thinker.audio_tower", "thinker.visual", "talker", "token2wav"],
        "embedding_dimension_default": args.dimension,
        "native_hidden_size": 2048,
        "note": "1024 is a prefix projection for the smoke/service path; use the upstream elastic projection when available.",
    }
    (args.destination / "quantization_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
