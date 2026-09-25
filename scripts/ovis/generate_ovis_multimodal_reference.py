"""Generate BF16 reference embeddings for the small Ovis media fixture."""

from __future__ import annotations

import argparse
import json
import math
import time
import wave
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as functional
from transformers import AutoProcessor
from transformers.models.qwen2_5_omni.modeling_qwen2_5_omni import (
    Qwen2_5OmniForConditionalGeneration,
)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("the fixture WAV must be mono signed 16-bit PCM")
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        return audio.astype(np.float32) / 32768.0, wav.getframerate()


def make_inputs(processor: AutoProcessor, kind: str, path: Path) -> dict[str, object]:
    conversation = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Retrieve the {kind} sample."},
                {"type": kind, kind: str(path)},
            ],
        }
    ]
    text = processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=False)
    if kind == "audio":
        audio, sampling_rate = read_wav(path)
        return dict(processor(text=[text], audio=[audio], sampling_rate=sampling_rate, return_tensors="pt"))
    if kind == "image":
        return dict(processor(text=[text], images=[str(path)], return_tensors="pt"))
    if kind == "video":
        return dict(processor(text=[text], videos=[str(path)], return_tensors="pt"))
    raise ValueError(f"unsupported kind: {kind}")


def move_tensors(inputs: dict[str, object], device: torch.device) -> dict[str, object]:
    return {
        key: value.to(device) if isinstance(value, torch.Tensor) else value
        for key, value in inputs.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("media_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dimension", type=int, default=2048)
    parser.add_argument("--gpu-memory", default="7.2GiB")
    parser.add_argument("--cpu-memory", default="8GiB")
    args = parser.parse_args()

    processor = AutoProcessor.from_pretrained(args.model_dir, local_files_only=True)
    model = Qwen2_5OmniForConditionalGeneration.from_pretrained(
        args.model_dir,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        max_memory={0: args.gpu_memory, "cpu": args.cpu_memory},
        low_cpu_mem_usage=True,
    ).eval()
    input_device = model.thinker.get_input_embeddings().weight.device
    results: dict[str, object] = {}
    started = time.perf_counter()
    for kind in ("image", "audio", "video"):
        path = args.media_dir / {
            "image": "sample_diagram.png",
            "audio": "sample_tone.wav",
            "video": "sample_clip.mp4",
        }[kind]
        item_started = time.perf_counter()
        inputs = move_tensors(make_inputs(processor, kind, path), input_device)
        with torch.inference_mode():
            output = model.thinker(
                **inputs,
                output_hidden_states=True,
                return_dict=True,
                use_cache=False,
            )
        hidden = output.hidden_states[-1]
        attention_mask = inputs.get("attention_mask")
        if not isinstance(attention_mask, torch.Tensor):
            selected = hidden[:, -1, :]
        else:
            last = attention_mask.to(torch.int64).sum(dim=1).sub(1).clamp_min(0)
            selected = hidden[torch.arange(hidden.shape[0], device=hidden.device), last]
        vector = functional.normalize(selected[..., : args.dimension].float(), p=2, dim=-1)[0]
        values = vector.cpu().tolist()
        results[kind] = {
            "fixture": str(path),
            "dimensions": len(values),
            "seconds": time.perf_counter() - item_started,
            "finite": all(math.isfinite(value) for value in values),
            "norm": math.sqrt(sum(value * value for value in values)),
            "vector": values,
        }
        print(json.dumps({"kind": kind, "seconds": results[kind]["seconds"]}), flush=True)

    output = {
        "model": str(args.model_dir),
        "dtype": "bfloat16",
        "dimensions": args.dimension,
        "seconds": time.perf_counter() - started,
        "media": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output) + "\n", encoding="utf-8")
    print(json.dumps({"dimensions": args.dimension, "seconds": output["seconds"]}))


if __name__ == "__main__":
    main()
