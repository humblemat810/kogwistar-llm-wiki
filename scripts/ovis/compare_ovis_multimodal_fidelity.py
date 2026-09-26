"""Compare the vLLM media endpoint with persisted BF16 media references."""

from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import time
from pathlib import Path
from urllib.request import Request, urlopen


def data_url(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{media_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def query(url: str, kind: str, path: Path) -> dict[str, object]:
    if kind == "image":
        content = [{"type": "text", "text": "Retrieve the image sample."}, {"type": "image_url", "image_url": {"url": data_url(path)}}]
    elif kind == "audio":
        content = [{"type": "text", "text": "Retrieve the audio sample."}, {"type": "input_audio", "input_audio": {"data": base64.b64encode(path.read_bytes()).decode("ascii"), "format": "wav"}}]
    elif kind == "video":
        content = [{"type": "text", "text": "Retrieve the video sample."}, {"type": "video_url", "video_url": {"url": data_url(path)}}]
    else:
        raise ValueError(kind)
    body = json.dumps({"model": "/model", "input": [{"role": "user", "content": content}]}).encode()
    request = Request(url.rstrip("/") + "/v1/embeddings", data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urlopen(request, timeout=600) as response:
        payload = json.load(response)
    vector = payload["data"][0]["embedding"]
    return {"vector": vector, "seconds": time.perf_counter() - started}


def metrics(candidate: list[float], reference: list[float]) -> dict[str, float]:
    dot = sum(left * right for left, right in zip(candidate, reference))
    cosine = max(-1.0, min(1.0, dot))
    return {
        "cosine": cosine,
        "cosine_drift": 1.0 - cosine,
        "l2_distance": math.sqrt(sum((left - right) ** 2 for left, right in zip(candidate, reference))),
        "candidate_norm": math.sqrt(sum(value * value for value in candidate)),
        "reference_norm": math.sqrt(sum(value * value for value in reference)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("media_dir", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    reference = json.loads(args.reference.read_text(encoding="utf-8"))["media"]
    names = {"image": "sample_diagram.png", "audio": "sample_tone.wav", "video": "sample_clip.mp4"}
    results: dict[str, object] = {}
    for kind, filename in names.items():
        candidate = query(args.url, kind, args.media_dir / filename)
        reference_vector = reference[kind]["vector"][: len(candidate["vector"])]
        norm = math.sqrt(sum(value * value for value in reference_vector))
        reference_vector = [value / norm for value in reference_vector]
        item = {
            "fixture": filename,
            "dimensions": len(candidate["vector"]),
            "seconds": candidate["seconds"],
            "metrics_vs_bf16": metrics(candidate["vector"], reference_vector),
        }
        results[kind] = item
        print(json.dumps({"kind": kind, **item["metrics_vs_bf16"]}), flush=True)

    output = {"url": args.url, "reference": str(args.reference), "media": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
