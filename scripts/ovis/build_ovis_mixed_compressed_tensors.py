"""Build an experimental mixed W2/W4/W8 compressed-tensors checkpoint.

This is a model-free RTN baseline, not GPTQ: it deliberately creates the
requested bit allocation without loading the multimodal model definition. The
separate GPTQ calibration experiment must still validate whether the custom
Qwen2.5-Omni architecture can be calibrated safely.
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


TEXT_IGNORE = [
    r"re:^(thinker\.(audio_tower|visual)|talker|token2wav)\..*",
]
GROUPS = {
    "W2A16": [r"re:.*layers\.(?:[0-9]|1[0-3])\..*"],
    "W4A16": [r"re:.*layers\.(?:1[4-9]|2[0-6])\..*"],
    "W8A16": [r"re:.*layers\.27\..*"],
}

HIGH_LOW_HIGH_GROUPS = {
    "W8A16": [
        r"re:.*layers\.(?:[0-5])\..*",
        r"re:.*layers\.(?:2[2-7])\..*",
    ],
    "W2A16": [r"re:.*layers\.(?:[6-9]|1[0-9]|2[0-1])\..*"],
}

# A smaller high-low-high variant: only the first and last two blocks stay at
# W8A16, while the 24 interior blocks use W2A16. This keeps more precision at
# the network boundaries without spending twelve blocks on W8A16.
HIGH_LOW_HIGH_LITE_GROUPS = {
    "W8A16": [
        r"re:.*layers\.(?:[0-1])\..*",
        r"re:.*layers\.(?:2[6-7])\..*",
    ],
    "W2A16": [r"re:.*layers\.(?:[2-9]|1[0-9]|2[0-5])\..*"],
}

HIGH_MID_HIGH_GROUPS = {
    "W8A16": [
        r"re:.*layers\.(?:[0-5])\..*",
        r"re:.*layers\.(?:2[2-7])\..*",
    ],
    "W4A16": [r"re:.*layers\.(?:[6-9]|1[0-9]|2[0-1])\..*"],
}

# Boundary-preserving layout using W4 at the beginning/end and W2 in the
# middle. W4 is the highest boundary precision that avoids the early-W8
# packed-parameter mapping issue in the tested vLLM compressed-tensors loader.
HIGH_LOW_HIGH_W4_GROUPS = {
    "W4A16": [
        r"re:.*layers\.(?:[0-5])\..*",
        r"re:.*layers\.(?:2[2-7])\..*",
    ],
    "W2A16": [r"re:.*layers\.(?:[6-9]|1[0-9]|2[0-1])\..*"],
}

# vLLM's tested compressed-tensors path cannot map a quantized first block
# when it is W4/W8. Leave layers 0-1 in BF16, use W2 through the middle, and
# retain W4 for the final six blocks as the loader-compatible boundary scheme.
BOUNDARY_BF16_W2_W4_GROUPS = {
    "W2A16": [r"re:.*layers\.(?:[2-9]|1[0-9]|2[0-1])\..*"],
    "W4A16": [r"re:.*layers\.(?:2[2-7])\..*"],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source-index", type=int, default=None)
    parser.add_argument("--start-output", type=int, default=None)
    parser.add_argument(
        "--arrangement",
        choices=["w2-w4-w8", "high-low-high", "high-low-high-lite", "high-mid-high", "high-low-high-w4", "boundary-bf16-w2-w4"],
        default="w2-w4-w8",
    )
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)

    groups = {
        "w2-w4-w8": GROUPS,
        "high-low-high": HIGH_LOW_HIGH_GROUPS,
        "high-low-high-lite": HIGH_LOW_HIGH_LITE_GROUPS,
        "high-mid-high": HIGH_MID_HIGH_GROUPS,
        "high-low-high-w4": HIGH_LOW_HIGH_W4_GROUPS,
        "boundary-bf16-w2-w4": BOUNDARY_BF16_W2_W4_GROUPS,
    }[args.arrangement]
    config = QuantizationConfig(config_groups=groups, ignore=TEXT_IGNORE)
    converter = ModelFreePtqConverter(config)
    source_files = sorted(args.source.glob("*.safetensors"))
    if args.source_index is not None:
        source_files = [source_files[args.source_index]]
    existing_index = args.destination / "model.safetensors.index.json"
    if existing_index.exists():
        prior = json.loads(existing_index.read_text(encoding="utf-8"))
        weight_map = dict(prior.get("weight_map", {}))
        total_size = int(prior.get("metadata", {}).get("total_size", 0))
    else:
        weight_map = {}
        total_size = 0
    matched = {name: 0 for name in groups}

    output_index = args.start_output or 1
    chunk: dict[str, object] = {}
    chunk_keys = 0

    def flush_chunk() -> None:
        nonlocal output_index, chunk, chunk_keys, total_size
        if not chunk:
            return
        output_name = f"model-{output_index:05d}.safetensors"
        output_path = args.destination / output_name
        save_file(chunk, str(output_path), metadata={"format": "pt"})
        total_size += output_path.stat().st_size
        weight_map.update({name: output_name for name in chunk})
        print(json.dumps({"output": output_name, "keys": len(chunk)}), flush=True)
        output_index += 1
        chunk = {}
        chunk_keys = 0
        gc.collect()

    for source_file in source_files:
        with safe_open(str(source_file), framework="pt", device="cpu") as handle:
            for name in handle.keys():
                module_name = name.rsplit(".", 1)[0]
                for group, targets in groups.items():
                    if any(
                        re.match(target.removeprefix("re:"), module_name)
                        for target in targets
                    ):
                        matched[group] += 1
                        break
                chunk[name] = handle.get_tensor(name)
                chunk_keys += 1
                if chunk_keys >= 32:
                    compressed = converter.process(chunk)
                    chunk = compressed
                    flush_chunk()
        gc.collect()
    if chunk:
        flush_chunk()

    index_payload = {
        "metadata": {"total_size": total_size, "total_parameters": len(weight_map)},
        "weight_map": weight_map,
    }
    (args.destination / "model.safetensors.index.json").write_text(
        json.dumps(index_payload, indent=2) + "\n", encoding="utf-8"
    )

    for item in args.source.iterdir():
        if item.is_file() and not item.name.endswith(".safetensors") and item.name != "model.safetensors.index.json":
            shutil.copy2(item, args.destination / item.name)

    model_config_path = args.destination / "config.json"
    model_config = json.loads(model_config_path.read_text(encoding="utf-8"))
    final_config = converter.update_config(None).model_dump(mode="json")
    final_config["format"] = "mixed-precision"
    for scheme in final_config["config_groups"].values():
        scheme["format"] = "pack-quantized"
    model_config["quantization_config"] = final_config
    model_config["is_matryoshka"] = True
    model_config["matryoshka_dimensions"] = [1024, 2048]
    model_config_path.write_text(json.dumps(model_config, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total_size": total_size, "total_keys": len(weight_map), "matched": matched}), flush=True)


if __name__ == "__main__":
    main()
