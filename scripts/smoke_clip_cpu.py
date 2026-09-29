"""Smoke-test the standalone CLIP CPU embedding endpoint with synthetic data."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw


def _synthetic_image() -> bytes:
    image = Image.new("RGB", (224, 224), (28, 34, 44))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 55, 179, 169), radius=14, fill=(62, 95, 116))
    draw.rectangle((72, 82, 152, 142), fill=(177, 185, 142))
    draw.line((30, 190, 194, 190), fill=(216, 163, 70), width=5)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def _request(url: str, *, token: str | None = None, payload: dict[str, object] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"embedding service returned HTTP {exc.code}: {exc.read(1024)!r}") from exc


def main() -> int:
    base_url = os.environ.get("LLM_WIKI_CLIP_URL", "http://127.0.0.1:8792").rstrip("/")
    token = os.environ.get("LLM_WIKI_CLIP_TOKEN") or None
    readiness = _request(f"{base_url}/readyz", token=token)
    capabilities = _request(f"{base_url}/v1/capabilities", token=token)
    profile = readiness["profile"]
    raw_image = _synthetic_image()
    image_asset = {
        "data_base64": base64.b64encode(raw_image).decode("ascii"),
        "content_type": "image/png",
        "sha256": hashlib.sha256(raw_image).hexdigest(),
    }
    payload = {
        "contract_version": "v1",
        "request_id": "clip-cpu-smoke",
        "operation": "query",
        "profile_fingerprint": profile["fingerprint"],
        "items": [
            {
                "item_id": "finance-text",
                "modality": "text",
                "text": "NVIDIA and AMD design GPUs used for AI training and data-center workloads.",
            },
            {"item_id": "synthetic-image", "modality": "image", "asset": image_asset},
            {
                "item_id": "mixed-text-image",
                "modality": "image",
                "text": "A semiconductor package with a GPU die and memory components.",
                "asset": image_asset,
            },
        ],
    }
    started = time.perf_counter()
    response = _request(f"{base_url}/v1/represent", token=token, payload=payload)
    elapsed = time.perf_counter() - started
    results = response["results"]
    if [item["item_id"] for item in results] != ["finance-text", "synthetic-image", "mixed-text-image"]:
        raise RuntimeError("embedding service changed item ordering or dropped an item")
    dimension = int(profile["dimension"])
    for item in results:
        vectors = item["vectors"]
        if len(vectors) != 1 or len(vectors[0]) != dimension:
            raise RuntimeError(f"invalid vector shape for {item['item_id']}")
        norm = math.sqrt(sum(float(value) ** 2 for value in vectors[0]))
        if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise RuntimeError(f"vector for {item['item_id']} is not normalized: {norm}")
    print(f"model={profile['model']} revision={profile['model_revision']}")
    print(f"encoder=CLIP dual projection dimension={dimension} fingerprint={profile['fingerprint']}")
    print(f"modalities={','.join(capabilities['modalities'])} elapsed_seconds={elapsed:.3f}")
    print("items=3 vectors=3 all_dimensions_and_norms_valid=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
