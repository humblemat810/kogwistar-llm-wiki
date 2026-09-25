"""Merge a GPTQ-compressed Thinker text model into the full Ovis bundle."""

from __future__ import annotations

import argparse
import gc
import json
import shutil
from pathlib import Path

from safetensors import safe_open
from safetensors.torch import save_file


def write_chunks(items, output: Path, max_bytes: int = 32 * 1024 * 1024) -> None:
    output.mkdir(parents=True, exist_ok=True)
    chunk: dict[str, object] = {}
    chunk_bytes = 0
    part = 1
    total_keys = 0

    def flush() -> None:
        nonlocal chunk, chunk_bytes, part
        if not chunk:
            return
        save_file(chunk, str(output / f"model-{part:05d}.safetensors"), metadata={"format": "pt"})
        part += 1
        chunk = {}
        chunk_bytes = 0
        gc.collect()

    for name, tensor in items:
        total_keys += 1
        size = tensor.numel() * tensor.element_size()
        if chunk and chunk_bytes + size > max_bytes:
            flush()
        chunk[name] = tensor
        chunk_bytes += size
    flush()
    print(f"wrote {total_keys} tensors in {part - 1} shards", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--gptq-thinker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--repaired-scales",
        type=Path,
        default=None,
        help="Optional safetensors file replacing exported GPTQ weight_scale tensors.",
    )
    parser.add_argument(
        "--overlay-original",
        action="store_true",
        help="Copy original shards unchanged and add GPTQ keys as an overlay.",
    )
    args = parser.parse_args()

    original = args.original.resolve()
    gptq = args.gptq_thinker.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    # Copy tokenizer, processor, templates, and license files from the full model.
    for path in original.iterdir():
        if not path.is_file():
            continue
        if path.suffix == ".safetensors":
            continue
        if path.name in {"config.json", "generation_config.json", "model.safetensors.index.json"}:
            continue
        shutil.copy2(path, output / path.name)

    def source_items():
        for shard in sorted(original.glob("*.safetensors")):
            print(f"reading original shard {shard.name}", flush=True)
            with safe_open(str(shard), framework="pt", device="cpu") as handle:
                for key in handle.keys():
                    # Replace the original FP/BF16 Thinker text transformer.
                    if key.startswith("thinker.model."):
                        continue
                    yield key, handle.get_tensor(key)
        print("reading GPTQ thinker shard", flush=True)
        with safe_open(str(gptq / "model.safetensors"), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                # The GPTQ output also contains copies of the audio/vision
                # towers. Keep the original full-model names and tensors for
                # those components; import only the text model subtree.
                if (
                    key.startswith("model.")
                    and key != "model.lm_head.weight"
                    and not (args.repaired_scales and key.endswith(".weight_scale"))
                ):
                    yield "thinker." + key, handle.get_tensor(key)
        if args.repaired_scales:
            with safe_open(str(args.repaired_scales), framework="pt", device="cpu") as handle:
                for key in handle.keys():
                    yield key, handle.get_tensor(key)

    if args.overlay_original:
        # Avoid holding the 11-GB original checkpoint in one Python process.
        # vLLM selects the packed keys described by quantization_config and
        # ignores the retained FP/BF16 counterparts as unexpected keys.
        for shard in sorted(original.glob("*.safetensors")):
            shutil.copy2(shard, output / shard.name)
        # Drop the text-model lm_head; Ovis' embedding thinker intentionally
        # has no lm_head and vLLM rejects an unexpected one.
        with safe_open(str(gptq / "model.safetensors"), framework="pt", device="cpu") as handle:
            gptq_items = {
                "thinker." + key: handle.get_tensor(key)
                for key in handle.keys()
                if key.startswith("model.") and key != "model.lm_head.weight"
            }
        save_file(gptq_items, str(output / "gptq-thinker.safetensors"), metadata={"format": "pt"})
    else:
        write_chunks(source_items(), output)

    with (original / "config.json").open(encoding="utf-8") as f:
        config = json.load(f)
    with (gptq / "config.json").open(encoding="utf-8") as f:
        gptq_config = json.load(f)

    quant = gptq_config["quantization_config"]
    # vLLM resolves the Ovis Thinker through its Qwen2Model child, whose
    # effective module names begin at `layers.*`, not `thinker.model.*`.
    for group in quant.get("config_groups", {}).values():
        group["targets"] = [
            target.replace(
                "(?:thinker\\.)?model\\.", ""
            ).replace("thinker\\.model\\.", "")
            for target in group.get("targets", [])
        ]
    quant["ignore"] = [
        r"re:^(thinker\.(audio_tower|visual)|talker|token2wav)\..*"
    ]
    config["quantization_config"] = quant
    config["is_matryoshka"] = True
    config["matryoshka_dimensions"] = [1024, 2048]
    with (output / "config.json").open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    shutil.copy2(original / "generation_config.json", output / "generation_config.json")


if __name__ == "__main__":
    main()
