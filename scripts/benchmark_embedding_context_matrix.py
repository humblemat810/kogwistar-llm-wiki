"""Compare embedding services over increasing text-context sizes.

The two supported service contracts are the standalone Qwen ``/v1/represent``
endpoint and the OpenAI-compatible vLLM ``/v1/embeddings`` endpoint used by
Ovis.  ``words`` is intentionally reported as a request-shape control, not as
an exact tokenizer count.  The report also records the requested character
count so server-side cropping is visible instead of being mistaken for full
long-context inference.

Example::

    python scripts/benchmark_embedding_context_matrix.py \
      --qwen-url http://127.0.0.1:8790 \
      --ovis-url http://127.0.0.1:8000 \
      --token "$LLM_WIKI_EMBEDDING_VLLM_TOKEN" \
      --lengths 128 512 2048 4096 8192 \
      --output test-results/context-matrix.json
"""

from __future__ import annotations

import argparse
import base64
from hashlib import sha256
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# A deterministic valid 1x1 PNG keeps image comparisons independent of fixture
# loading and makes the only changing input dimension the text context.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_PNG_B64 = base64.b64encode(_PNG).decode("ascii")


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    url: str
    kind: str
    token: str


def _headers(token: str, *, content_type: bool = False) -> dict[str, str]:
    result = {"Accept": "application/json"}
    if token:
        result["Authorization"] = f"Bearer {token}"
    if content_type:
        result["Content-Type"] = "application/json"
    return result


def _post(service: Service, path: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
    request = Request(
        service.url.rstrip("/") + path,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=_headers(service.token, content_type=True),
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("service response must be an object")
    return result


def _get(service: Service, path: str, timeout: float) -> dict[str, object]:
    request = Request(service.url.rstrip("/") + path, headers=_headers(service.token))
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("service response must be an object")
    return result


def _text(words: int) -> str:
    return " ".join(["context"] * words)


def _qwen_payload(service: Service, profile: dict[str, object], *, words: int, modality: str) -> dict[str, object]:
    item: dict[str, object] = {"item_id": f"{modality}-{words}", "modality": "image" if modality == "text_image" else "text"}
    if modality in {"text", "text_image"}:
        item["text"] = _text(words)
    if modality in {"image", "text_image"}:
        item["asset"] = {
            "data_base64": _PNG_B64,
            "content_type": "image/png",
            "sha256": sha256(_PNG).hexdigest(),
        }
    return {
        "contract_version": "v1",
        "request_id": f"context-matrix-{modality}-{words}",
        "operation": "document",
        "profile_fingerprint": profile["fingerprint"],
        "items": [item],
    }


def _ovis_input(*, words: int, modality: str) -> object:
    content: list[dict[str, object]] = []
    if modality in {"image", "text_image"}:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"}})
    if modality in {"text", "text_image"}:
        content.append({"type": "text", "text": _text(words)})
    return [{"role": "user", "content": content}]


def _vector(payload: dict[str, object], kind: str) -> list[float]:
    if kind == "qwen":
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise ValueError("Qwen response has no single result")
        vectors = results[0].get("vectors")
        if not isinstance(vectors, list) or len(vectors) != 1 or not isinstance(vectors[0], list):
            raise ValueError("Qwen response has no single vector")
        return [float(value) for value in vectors[0]]
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("vLLM response has no single result")
    vector = data[0].get("embedding")
    if not isinstance(vector, list):
        raise ValueError("vLLM response has no embedding")
    return [float(value) for value in vector]


def _request(service: Service, profile: dict[str, object] | None, *, words: int, modality: str, timeout: float) -> dict[str, object]:
    if service.kind == "qwen":
        if profile is None:
            raise ValueError("Qwen profile is required")
        return _post(service, "/v1/represent", _qwen_payload(service, profile, words=words, modality=modality), timeout)
    return _post(
        service,
        "/v1/embeddings",
        {"model": str((profile or {}).get("model", service.name)), "encoding_format": "float", "input": _ovis_input(words=words, modality=modality)},
        timeout,
    )


def _measure(service: Service, profile: dict[str, object] | None, *, words: int, modality: str, repeats: int, warmups: int, timeout: float) -> dict[str, object]:
    started = time.perf_counter()
    try:
        for _ in range(warmups):
            _vector(_request(service, profile, words=words, modality=modality, timeout=timeout), service.kind)
        timings: list[float] = []
        dimension = 0
        for _ in range(repeats):
            request_started = time.perf_counter()
            vector = _vector(_request(service, profile, words=words, modality=modality, timeout=timeout), service.kind)
            timings.append((time.perf_counter() - request_started) * 1000.0)
            dimension = len(vector)
        elapsed = median(timings)
        return {
            "service": service.name,
            "modality": modality,
            "requested_words": words,
            "requested_chars": len(_text(words)),
            "warmups": warmups,
            "repeats": repeats,
            "median_ms": round(elapsed, 2),
            "items_per_second": round(1000.0 / elapsed, 3),
            "dimension": dimension,
            "finite": all(math.isfinite(value) for value in vector),
            "status": "ok",
            "wall_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        return {
            "service": service.name,
            "modality": modality,
            "requested_words": words,
            "requested_chars": len(_text(words)),
            "warmups": warmups,
            "repeats": repeats,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "wall_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }


def _profile(service: Service, timeout: float) -> dict[str, object] | None:
    if service.kind == "qwen":
        ready = _get(service, "/readyz", timeout)
        profile = ready.get("profile")
        if not isinstance(profile, dict):
            raise ValueError("Qwen readiness omitted profile")
        return profile
    models = _get(service, "/v1/models", timeout)
    data = models.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ValueError("vLLM model response omitted model identity")
    model = data[0].get("id")
    if not isinstance(model, str) or not model:
        raise ValueError("vLLM model response omitted model id")
    return {"model": model}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen-url")
    parser.add_argument("--ovis-url")
    parser.add_argument("--token", default="", help="Shared token for both services")
    parser.add_argument("--qwen-token", default=None)
    parser.add_argument("--ovis-token", default=None)
    parser.add_argument("--lengths", type=int, nargs="+", default=[128, 512, 2048, 4096, 8192])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.qwen_url and not args.ovis_url:
        parser.error("at least one of --qwen-url or --ovis-url is required")
    if any(length <= 0 for length in args.lengths) or args.repeats <= 0 or args.warmups < 0:
        parser.error("lengths and repeats must be positive; warmups cannot be negative")

    services = []
    if args.qwen_url:
        services.append(Service("Qwen3-VL-Embedding-2B", args.qwen_url, "qwen", args.qwen_token if args.qwen_token is not None else args.token))
    if args.ovis_url:
        services.append(Service("Ovis-Omni-Embedding", args.ovis_url, "ovis", args.ovis_token if args.ovis_token is not None else args.token))
    output: dict[str, object] = {"length_unit": "repeated words (not exact tokenizer tokens)", "lengths": args.lengths, "services": [], "results": []}
    for service in services:
        try:
            profile = _profile(service, args.timeout)
            output["services"].append({"name": service.name, "kind": service.kind, "url": service.url, "profile": profile})
        except (HTTPError, URLError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            output["services"].append({"name": service.name, "kind": service.kind, "url": service.url, "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"})
            continue
        for modality in ("text", "image", "text_image"):
            lengths = args.lengths if modality != "image" else [args.lengths[0]]
            for words in lengths:
                output["results"].append(_measure(service, profile, words=words, modality=modality, repeats=args.repeats, warmups=args.warmups, timeout=args.timeout))

    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
