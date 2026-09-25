"""Split an all-layer Transformers BnB checkpoint into vLLM-compatible files.

vLLM's Qwen2.5-Omni implementation expects the multimodal towers in their
original shapes. Keep quantized Thinker language weights from the BnB artifact
and restore the original audio/vision tower tensors, then write a fresh index.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file


NON_TEXT_PREFIXES = ("thinker.audio_tower.", "thinker.visual.")


def keys_in(path: Path) -> list[str]:
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        return list(handle.keys())


def copy_selected(source: Path, destination: Path, selected: list[str]) -> None:
    tensors = {}
    with safe_open(str(source), framework="pt", device="cpu") as handle:
        for name in selected:
            tensors[name] = handle.get_tensor(name)
    save_file(tensors, str(destination), metadata={"format": "pt"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("original", type=Path)
    parser.add_argument("quantized", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)

    quant_index = json.loads(
        (args.quantized / "model.safetensors.index.json").read_text(encoding="utf-8")
    )
    quant_sources = sorted(set(quant_index["weight_map"].values()))

    original_index = json.loads(
        (args.original / "model.safetensors.index.json").read_text(encoding="utf-8")
    )
    source_files = sorted(set(original_index["weight_map"].values()))
    weight_map: dict[str, str] = {}
    total_size = 0

    for number, source_name in enumerate(quant_sources, start=1):
        source_path = args.quantized / source_name
        text_keys = [
            name for name in keys_in(source_path)
            if not name.startswith(NON_TEXT_PREFIXES)
        ]
        output_name = f"text-quantized-{number:05d}.safetensors"
        output_path = args.destination / output_name
        copy_selected(source_path, output_path, text_keys)
        total_size += output_path.stat().st_size
        weight_map.update({name: output_name for name in text_keys})

    for number, source_name in enumerate(source_files, start=1):
        source_path = args.original / source_name
        with safe_open(str(source_path), framework="pt", device="cpu") as handle:
            mm_keys = [
                name
                for name in handle.keys()
                if name.startswith(NON_TEXT_PREFIXES)
            ]
        if not mm_keys:
            continue
        output_name = f"multimodal-{number:05d}.safetensors"
        output_path = args.destination / output_name
        copy_selected(source_path, output_path, mm_keys)
        total_size += output_path.stat().st_size
        weight_map.update({name: output_name for name in mm_keys})

    for item in args.quantized.iterdir():
        if item.is_file() and item.name not in {
            "model.safetensors.index.json",
        } and not item.name.endswith(".safetensors"):
            target = args.destination / item.name
            if not target.exists():
                shutil.copy2(item, target)

    (args.destination / "model.safetensors.index.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "total_size": total_size,
                    "total_parameters": len(weight_map),
                },
                "weight_map": weight_map,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"total_keys": len(weight_map), "bytes": total_size}))


if __name__ == "__main__":
    main()
