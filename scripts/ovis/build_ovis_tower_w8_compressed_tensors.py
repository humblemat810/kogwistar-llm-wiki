"""Build a tower-only W8A16 compressed-tensors Ovis checkpoint.

This is a model-free RTN screen.  It deliberately changes only the selected
multimodal tower and preserves the Thinker, Talker, and token2wav tensors in
their source dtype so vLLM runtime behavior can be isolated from Thinker
quantization.
"""

from __future__ import annotations

import argparse
import gc
import json
import re
import shutil
from pathlib import Path

from compressed_tensors.quantization import QuantizationConfig
from llmcompressor.entrypoints.model_free.converter import ModelFreePtqConverter
from safetensors import safe_open
from safetensors.torch import save_file


TOWER_PATTERNS = {
    # Only 2-D learned projections are eligible for the packed W8 path.
    # Norm vectors, biases, and convolutional tensors remain in source dtype.
    "vision": [
        r"re:^(?:thinker\.)?visual\.(?:blocks\.[0-9]+\.(?:attn\.(?:k|q|v|proj)|mlp\.(?:down_proj|gate_proj|up_proj))|merger\.mlp\.[02])$"
    ],
    "audio": [
        r"re:^(?:thinker\.)?audio_tower\..*(?:q_proj|k_proj|v_proj|out_proj|fc1|fc2)$"
    ],
    "both": [
        r"re:^(?:thinker\.)?visual\.(?:blocks\.[0-9]+\.(?:attn\.(?:k|q|v|proj)|mlp\.(?:down_proj|gate_proj|up_proj))|merger\.mlp\.[02])$",
        r"re:^(?:thinker\.)?audio_tower\..*(?:q_proj|k_proj|v_proj|out_proj|fc1|fc2)$",
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--tower", choices=sorted(TOWER_PATTERNS), required=True)
    parser.add_argument("--strategy", choices=("channel", "group"), default="channel")
    parser.add_argument("--group-size", type=int, default=128)
    parser.add_argument("--chunk-keys", type=int, default=32)
    args = parser.parse_args()

    if not args.source.is_dir():
        raise SystemExit(f"source directory does not exist: {args.source}")
    args.destination.mkdir(parents=True, exist_ok=True)
    if any(args.destination.iterdir()):
        raise SystemExit(f"destination is not empty: {args.destination}")

    targets = TOWER_PATTERNS[args.tower]
    config = QuantizationConfig(
        config_groups={
            "W8A16": {
                "targets": targets,
                "weights": {
                    "num_bits": 8,
                    "type": "int",
                    "symmetric": True,
                    "group_size": args.group_size if args.strategy == "group" else None,
                    "strategy": args.strategy,
                },
            }
        }
    )
    converter = ModelFreePtqConverter(config)
    matched = 0
    processed = 0
    output_index = 1
    chunk: dict[str, object] = {}
    weight_map: dict[str, str] = {}
    total_size = 0

    def flush() -> None:
        nonlocal output_index, chunk, total_size
        if not chunk:
            return
        name = f"model-{output_index:05d}.safetensors"
        path = args.destination / name
        # The compressed-tensors converter may return strided packed views;
        # safetensors requires each stored tensor to own a contiguous layout.
        chunk = {
            key: value.contiguous() if hasattr(value, "contiguous") else value
            for key, value in chunk.items()
        }
        save_file(chunk, str(path), metadata={"format": "pt"})
        total_size += path.stat().st_size
        weight_map.update({key: name for key in chunk})
        print(json.dumps({"output": name, "keys": len(chunk)}), flush=True)
        output_index += 1
        chunk = {}
        gc.collect()

    for source_file in sorted(args.source.glob("*.safetensors")):
        with safe_open(str(source_file), framework="pt", device="cpu") as handle:
            for name in handle.keys():
                processed += 1
                module_name = name.rsplit(".", 1)[0]
                if any(re.match(pattern.removeprefix("re:"), module_name) for pattern in targets):
                    matched += 1
                chunk[name] = handle.get_tensor(name)
                if len(chunk) >= args.chunk_keys:
                    chunk = converter.process(chunk)
                    flush()
        gc.collect()
    if chunk:
        chunk = converter.process(chunk)
        flush()

    index = {
        "metadata": {"total_size": total_size, "total_parameters": len(weight_map)},
        "weight_map": weight_map,
    }
    (args.destination / "model.safetensors.index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )

    for item in args.source.iterdir():
        if item.is_file() and item.suffix != ".safetensors":
            shutil.copy2(item, args.destination / item.name)

    config_path = args.destination / "config.json"
    model_config = json.loads(config_path.read_text(encoding="utf-8"))
    quantization_config = converter.update_config(None).model_dump(mode="json")
    quantization_config["format"] = "mixed-precision"
    for scheme in quantization_config["config_groups"].values():
        scheme["format"] = "pack-quantized"
    model_config["quantization_config"] = quantization_config
    model_config["is_matryoshka"] = True
    model_config["matryoshka_dimensions"] = [1024, 2048]
    config_path.write_text(json.dumps(model_config, indent=2) + "\n", encoding="utf-8")
    metadata = {
        "source": str(args.source),
        "algorithm": "RTN/model-free compressed-tensors",
        "tower": args.tower,
        "weights": "W8A16",
        "strategy": args.strategy,
        "group_size": args.group_size if args.strategy == "group" else None,
        "matched_tensors": matched,
        "processed_tensors": processed,
        "thinker_talker_token2wav": "preserved source dtype",
    }
    (args.destination / "quantization_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata), flush=True)


if __name__ == "__main__":
    main()
