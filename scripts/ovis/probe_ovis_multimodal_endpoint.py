"""Probe vLLM pooling embeddings with small local image/audio/video inputs."""

from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def data_url(path: Path) -> str:
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def request(url: str, content: list[dict[str, object]]) -> dict[str, object]:
    body = json.dumps(
        {
            "model": "/model",
            "input": [{"role": "user", "content": content}],
        }
    ).encode()
    req = Request(url.rstrip("/") + "/v1/embeddings", data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=600) as response:
            payload = json.load(response)
        vector = payload["data"][0]["embedding"]
        return {
            "status": "accepted",
            "seconds": time.perf_counter() - started,
            "dimensions": len(vector),
            "finite": all(math.isfinite(value) for value in vector),
            "norm": math.sqrt(sum(value * value for value in vector)),
        }
    except HTTPError as error:
        return {
            "status": "rejected",
            "seconds": time.perf_counter() - started,
            "http_status": error.code,
            "error": error.read().decode("utf-8", errors="replace")[:2000],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("media_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    paths = {
        "image": args.media_dir / "sample_diagram.png",
        "audio": args.media_dir / "sample_tone.wav",
        "video": args.media_dir / "sample_clip.mp4",
    }
    probes = {
        "image": [{"type": "text", "text": "Retrieve the diagram."}, {"type": "image_url", "image_url": {"url": data_url(paths["image"])}}],
        "audio": [{"type": "text", "text": "Retrieve the sound."}, {"type": "input_audio", "input_audio": {"data": base64.b64encode(paths["audio"].read_bytes()).decode("ascii"), "format": "wav"}}],
        "video": [{"type": "text", "text": "Retrieve the video."}, {"type": "video_url", "video_url": {"url": data_url(paths["video"])}}],
    }
    output = {"url": args.url, "media": {name: request(args.url, content) for name, content in probes.items()}}
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
