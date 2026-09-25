"""Generate BF16 reference vectors for the Ovis screening fixture."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
import torch.nn.functional as functional
from transformers import AutoTokenizer
from transformers.models.qwen2_5_omni.modeling_qwen2_5_omni import Qwen2_5OmniForConditionalGeneration


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dimension", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--gpu-memory", default="7.2GiB")
    parser.add_argument("--cpu-memory", default="8GiB")
    args = parser.parse_args()

    records = json.loads(args.benchmark.read_text(encoding="utf-8"))
    texts = [text for record in records for text in [record["query"], *record["candidates"]]]
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True)
    model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        args.model_dir,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        max_memory={0: args.gpu_memory, "cpu": args.cpu_memory},
        low_cpu_mem_usage=True,
    ).eval()
    input_device = model.thinker.get_input_embeddings().weight.device
    vectors: list[list[float]] = []
    started = time.perf_counter()
    for start in range(0, len(texts), args.batch_size):
        batch = texts[start : start + args.batch_size]
        inputs = tokenizer(batch, return_tensors="pt", padding=True)
        inputs = {
            key: value.to(input_device) if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        with torch.inference_mode():
            output = model.thinker(
                **inputs,
                output_hidden_states=True,
                return_dict=True,
                use_cache=False,
            )
        hidden = output.hidden_states[-1]
        attention_mask = inputs.get("attention_mask")
        if attention_mask is None:
            selected = hidden[:, -1, :]
        else:
            last = attention_mask.to(torch.int64).sum(dim=1).sub(1).clamp_min(0)
            selected = hidden[torch.arange(hidden.shape[0], device=hidden.device), last]
        selected = functional.normalize(selected[..., : args.dimension].float(), p=2, dim=-1)
        vectors.extend(selected.cpu().tolist())
        if start == 0 or (start // args.batch_size) % 25 == 0:
            print(json.dumps({"processed": len(vectors), "total": len(texts)}), flush=True)

    output = {
        "model": str(args.model_dir),
        "dtype": "bfloat16",
        "fixture": str(args.benchmark),
        "vectors": vectors,
        "dimensions": len(vectors[0]),
        "seconds": time.perf_counter() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("dimensions", "seconds")}))


if __name__ == "__main__":
    main()
