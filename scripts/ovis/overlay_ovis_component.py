"""Replace one Ovis component in a packed checkpoint without loading all weights."""

from __future__ import annotations

import argparse
import gc
import json
import shutil
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("replacement", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"output is not empty: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)

    chunk: dict[str, object] = {}
    weight_map: dict[str, str] = {}
    total_size = 0
    part = 1

    def flush() -> None:
        nonlocal chunk, part, total_size
        if not chunk:
            return
        chunk = {
            key: value.contiguous() if hasattr(value, "contiguous") else value
            for key, value in chunk.items()
        }
        name = f"model-{part:05d}.safetensors"
        path = args.output / name
        save_file(chunk, str(path), metadata={"format": "pt"})
        total_size += path.stat().st_size
        weight_map.update({key: name for key in chunk})
        print(json.dumps({"output": name, "keys": len(chunk)}), flush=True)
        part += 1
        chunk = {}
        gc.collect()

    for shard in sorted(args.base.glob("*.safetensors")):
        with safe_open(str(shard), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                if key.startswith(args.prefix):
                    continue
                chunk[key] = handle.get_tensor(key)
                if len(chunk) >= 64:
                    flush()

    replacement_keys = 0
    for shard in sorted(args.replacement.glob("*.safetensors")):
        with safe_open(str(shard), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                if not key.startswith(args.prefix):
                    continue
                chunk[key] = handle.get_tensor(key)
                replacement_keys += 1
                if len(chunk) >= 64:
                    flush()
    flush()

    if replacement_keys == 0:
        raise SystemExit(f"replacement contains no tensors with prefix {args.prefix!r}")
    index = {
        "metadata": {"total_size": total_size, "total_parameters": len(weight_map)},
        "weight_map": weight_map,
    }
    (args.output / "model.safetensors.index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    for item in args.base.iterdir():
        if item.is_file() and item.suffix != ".safetensors" and item.name != "model.safetensors.index.json":
            shutil.copy2(item, args.output / item.name)

    config = json.loads((args.base / "config.json").read_text(encoding="utf-8"))
    replacement_config = json.loads((args.replacement / "config.json").read_text(encoding="utf-8"))
    quant = config.get("quantization_config")
    replacement_quant = replacement_config.get("quantization_config", {})
    if quant and replacement_quant:
        groups = quant.setdefault("config_groups", {})
        tower_group = next(iter(replacement_quant.get("config_groups", {}).values()))
        groups["W8A16_TOWER"] = tower_group
        quant["ignore"] = [
            pattern for pattern in quant.get("ignore", [])
            if "visual" not in pattern
        ]
        quant["ignore"].append(r"re:^(thinker\.(audio_tower)|talker|token2wav)\..*")
        config["quantization_config"] = quant
    (args.output / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    metadata = {
        "base": str(args.base),
        "replacement": str(args.replacement),
        "replaced_prefix": args.prefix,
        "replacement_tensors": replacement_keys,
    }
    (args.output / "component_overlay_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata), flush=True)


if __name__ == "__main__":
    main()
