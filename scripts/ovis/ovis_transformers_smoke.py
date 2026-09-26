"""Minimal text embedding smoke test for a pinned Ovis-Omni checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from transformers import AutoProcessor
from transformers.models.qwen2_5_omni.modeling_qwen2_5_omni import Qwen2_5OmniForConditionalGeneration


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--dimension", type=int, default=1024)
    args = parser.parse_args()

    processor = AutoProcessor.from_pretrained(args.model_dir, local_files_only=True)
    model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        args.model_dir,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        low_cpu_mem_usage=True,
    ).eval()
    prompt = "Represent the user's input.\nA small test sentence for embedding."
    inputs = processor(text=prompt, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) if hasattr(value, "to") else value for key, value in inputs.items()}
    thinker = model.thinker
    with torch.inference_mode():
        output = thinker(**inputs, output_hidden_states=True, return_dict=True, use_cache=False)
    hidden = output.hidden_states[-1]
    attention_mask = inputs.get("attention_mask")
    if attention_mask is None:
        vector = hidden[:, -1, :]
    else:
        last = attention_mask.to(torch.int64).sum(dim=1).sub(1).clamp_min(0)
        vector = hidden[torch.arange(hidden.shape[0], device=hidden.device), last]
    vector = torch.nn.functional.normalize(vector[..., : args.dimension].float(), p=2, dim=-1)
    print(json.dumps({"shape": list(vector.shape), "norm": vector.norm(dim=-1).tolist(), "model_class": type(model).__name__}))


if __name__ == "__main__":
    main()
