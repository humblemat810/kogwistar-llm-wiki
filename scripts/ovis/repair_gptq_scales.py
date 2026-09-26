"""Recreate missing GPTQ group scales from the original BF16 weights."""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import save_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--gptq", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--default-bits", type=int, default=None)
    args = parser.parse_args()

    index = json.loads(
        (args.original / "model.safetensors.index.json").read_text(encoding="utf-8")
    )["weight_map"]
    scales: dict[str, torch.Tensor] = {}
    with safe_open(str(args.gptq), framework="pt", device="cpu") as quantized:
        scale_specs = [
            (key, quantized.get_tensor(key).shape, quantized.get_tensor(key).dtype)
            for key in quantized.keys()
            if key.endswith(".weight_scale")
        ]
    for key, scale_shape, scale_dtype in scale_specs:
        print(f"repairing {key}", flush=True)
        source_key = "thinker." + key[: -len("_scale")]
        shard = index[source_key]
        with safe_open(str(args.original / shard), framework="pt", device="cpu") as handle:
            weight = handle.get_tensor(source_key).float()
            group_size = weight.shape[1] // scale_shape[1]
            if args.default_bits is not None:
                bits = args.default_bits
            elif ".layers.27." in key:
                bits = 8
            elif any(f".layers.{i}." in key for i in range(14)):
                bits = 2
            else:
                bits = 4
            levels = (1 << (bits - 1)) - 1
            scales["thinker." + key] = (
                weight.reshape(weight.shape[0], scale_shape[1], group_size)
                .abs()
                .amax(dim=2)
                / levels
            ).to(dtype=scale_dtype)
        del weight
        gc.collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_file(scales, str(args.output), metadata={"format": "pt"})
    gc.collect()
    print(f"wrote {len(scales)} repaired scales", flush=True)


if __name__ == "__main__":
    main()
