"""Inventory Ovis safetensors by runtime component without loading tensor data."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from safetensors import safe_open


DTYPE_BYTES = {
    "F64": 8,
    "F32": 4,
    "F16": 2,
    "BF16": 2,
    "I64": 8,
    "I32": 4,
    "I16": 2,
    "I8": 1,
    "U8": 1,
    "BOOL": 1,
}


def component(name: str) -> str:
    if name.startswith("thinker.visual.") or name.startswith("visual."):
        return "vision_tower"
    if name.startswith("thinker.audio_tower.") or name.startswith("audio_tower."):
        return "audio_tower"
    if name.startswith("thinker."):
        return "thinker"
    if name.startswith("talker."):
        return "talker"
    if name.startswith("token2wav."):
        return "token2wav"
    return "other"


def inventory(root: Path) -> dict[str, object]:
    totals: dict[str, dict[str, object]] = defaultdict(
        lambda: {"tensors": 0, "parameters": 0, "stored_bytes": 0, "dtypes": defaultdict(int)}
    )
    files = sorted(root.glob("*.safetensors"))
    for path in files:
        with safe_open(str(path), framework="pt", device="cpu") as handle:
            for name in handle.keys():
                view = handle.get_slice(name)
                shape = view.get_shape()
                dtype = str(view.get_dtype())
                numel = 1
                for dim in shape:
                    numel *= dim
                group = totals[component(name)]
                group["tensors"] = int(group["tensors"]) + 1
                group["parameters"] = int(group["parameters"]) + numel
                group["stored_bytes"] = int(group["stored_bytes"]) + numel * DTYPE_BYTES.get(dtype, 0)
                dtypes = group["dtypes"]
                assert isinstance(dtypes, defaultdict)
                dtypes[dtype] += 1

    result: dict[str, object] = {
        "root": str(root),
        "safetensors_files": len(files),
        "checkpoint_bytes": sum(path.stat().st_size for path in files),
        "components": {},
    }
    for name, values in sorted(totals.items()):
        values["dtypes"] = dict(values["dtypes"])
        result["components"][name] = values
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(inventory(args.root), indent=2))


if __name__ == "__main__":
    main()
