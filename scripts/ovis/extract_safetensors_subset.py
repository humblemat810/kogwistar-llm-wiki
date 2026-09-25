"""Extract a filtered safetensors shard without loading the whole checkpoint."""

from __future__ import annotations

import argparse
import gc
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-prefix", action="append", default=[])
    parser.add_argument("--skip-suffix", action="append", default=[])
    parser.add_argument("--require-prefix", action="append", default=[])
    parser.add_argument("--add-prefix", default="")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    chunk: dict[str, object] = {}
    chunk_bytes = 0
    part = 1

    def flush() -> None:
        nonlocal chunk, chunk_bytes, part
        if not chunk:
            return
        save_file(
            chunk,
            str(args.output / f"part-{part:05d}.safetensors"),
            metadata={"format": "pt"},
        )
        part += 1
        chunk = {}
        chunk_bytes = 0
        gc.collect()

    with safe_open(str(args.input), framework="pt", device="cpu") as handle:
        for key in handle.keys():
            if any(key.startswith(prefix) for prefix in args.skip_prefix):
                continue
            if any(key.endswith(suffix) for suffix in args.skip_suffix):
                continue
            if args.require_prefix and not any(
                key.startswith(prefix) for prefix in args.require_prefix
            ):
                continue
            tensor = handle.get_tensor(key)
            output_key = args.add_prefix + key
            size = tensor.numel() * tensor.element_size()
            if chunk and chunk_bytes + size > 32 * 1024 * 1024:
                flush()
            chunk[output_key] = tensor
            chunk_bytes += size
    flush()
    print(f"wrote {part - 1} shards", flush=True)


if __name__ == "__main__":
    main()
