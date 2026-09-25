"""Rebuild a safetensors index from the files in a generated bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from safetensors import safe_open


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    weight_map: dict[str, str] = {}
    total_size = 0
    for path in sorted(args.bundle.glob("*.safetensors")):
        total_size += path.stat().st_size
        with safe_open(str(path), framework="pt", device="cpu") as handle:
            weight_map.update({name: path.name for name in handle.keys()})
    (args.bundle / "model.safetensors.index.json").write_text(
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
    print(json.dumps({"files": len(list(args.bundle.glob('*.safetensors'))), "keys": len(weight_map), "bytes": total_size}))


if __name__ == "__main__":
    main()
