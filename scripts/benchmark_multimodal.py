"""Benchmark multimodal source encoding without requiring a provider by default.

The default fake mode measures API and batching overhead deterministically. Use
``--backend remote`` to measure the production representation service, or use
``--backend qwen3-vl`` with an installed local checkpoint for developer-only
comparison. ``colqwen`` remains an explicit legacy comparison route.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from io import BytesIO
import json
from statistics import mean, median
import time
from typing import Any
from urllib.request import Request, urlopen

from kogwistar_llm_wiki.multimodal_projection import (
    ColQwenNativeEncoder,
    Qwen3VLDenseEncoder,
    FakeMultimodalEncoder,
    MultimodalEncoder,
    MultimodalSourceUnit,
)
from kogwistar_llm_wiki.multimodal_sources import MappingAssetResolver


@dataclass(frozen=True, slots=True)
class MultimodalBenchmarkCase:
    name: str
    item_count: int
    samples: int
    median_ms: float
    mean_ms: float
    items_per_second: float


def _text_units(count: int) -> tuple[MultimodalSourceUnit, ...]:
    return tuple(
        MultimodalSourceUnit(
            view_id=f"benchmark-text-{index}",
            workspace_id="benchmark",
            source_id="benchmark-source",
            source_revision_id="benchmark-revision",
            modality="text",
            locator={"kind": "text_span", "start_char": index * 32, "end_char": (index + 1) * 32},
            text=f"A deterministic multimodal benchmark text passage number {index}.",
        )
        for index in range(count)
    )


def _image_units(count: int) -> tuple[MultimodalSourceUnit, ...]:
    return tuple(
        MultimodalSourceUnit(
            view_id=f"benchmark-image-{index}",
            workspace_id="benchmark",
            source_id="benchmark-source",
            source_revision_id="benchmark-revision",
            modality="image",
            locator={"kind": "whole_image", "ordinal": index},
            content_ref=f"benchmark://image/{index}",
        )
        for index in range(count)
    )


def _mixed_units(count: int) -> tuple[MultimodalSourceUnit, ...]:
    units: list[MultimodalSourceUnit] = []
    for index in range(count):
        units.append(MultimodalSourceUnit(
            view_id=f"benchmark-mixed-text-{index}",
            workspace_id="benchmark",
            source_id="benchmark-source",
            source_revision_id="benchmark-revision",
            modality="text",
            locator={"kind": "text_span", "start_char": index * 32, "end_char": (index + 1) * 32},
            text=f"Mixed benchmark text passage number {index}.",
        ))
        units.append(MultimodalSourceUnit(
            view_id=f"benchmark-mixed-image-{index}",
            workspace_id="benchmark",
            source_id="benchmark-source",
            source_revision_id="benchmark-revision",
            modality="image",
            locator={"kind": "whole_image", "ordinal": index},
            content_ref=f"benchmark://image/{index}",
        ))
    return tuple(units)


def _png_bytes() -> bytes:
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (32, 32), color="white").save(output, format="PNG")
    return output.getvalue()


def _measure_case(
    name: str,
    units: tuple[MultimodalSourceUnit, ...],
    encoder: MultimodalEncoder,
    *,
    batch_size: int,
    repeats: int,
    warmup: int,
    resolver: MappingAssetResolver | None,
) -> MultimodalBenchmarkCase:
    for _ in range(warmup):
        encoder.encode_documents(units, batch_size=batch_size, resolver=resolver)
    timings: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        result = encoder.encode_documents(units, batch_size=batch_size, resolver=resolver)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if len(result) != len(units):
            raise RuntimeError(f"{name} returned {len(result)} results for {len(units)} inputs")
        if encoder.profile.representation == "dense":
            for index, embedding in enumerate(result):
                if len(embedding) != 1 or len(embedding[0]) != encoder.profile.dimension:
                    raise RuntimeError(
                        f"{name} result {index} violates dense output shape: "
                        f"expected one vector of {encoder.profile.dimension} dimensions"
                    )
        timings.append(elapsed_ms)
    return MultimodalBenchmarkCase(
        name=name,
        item_count=len(units),
        samples=len(timings),
        median_ms=median(timings),
        mean_ms=mean(timings),
        items_per_second=len(units) / (median(timings) / 1000.0) if timings else 0.0,
    )


def run_multimodal_benchmark(
    *,
    backend: str = "fake",
    model_dir: str | None = None,
    device: str | None = None,
    batch_size: int = 4,
    items: int = 4,
    repeats: int = 3,
    warmup: int = 1,
    dimension: int = 1024,
    service_url: str | None = None,
    service_token: str | None = None,
    service_allowed_hosts: tuple[str, ...] = (),
    service_model: str | None = None,
    service_model_revision: str | None = None,
    service_instruction: str | None = None,
    service_batch_size: int | None = None,
) -> dict[str, Any]:
    """Run the five requested workload shapes and return JSON-ready metrics."""

    if items <= 0 or repeats <= 0 or warmup < 0 or batch_size <= 0:
        raise ValueError("items, repeats, and batch_size must be positive; warmup cannot be negative")
    if backend == "fake":
        encoder: MultimodalEncoder = FakeMultimodalEncoder()
        resolver = None
    elif backend == "colqwen":
        if not model_dir:
            raise ValueError("--model-dir is required for the colqwen backend")
        encoder = ColQwenNativeEncoder.from_pretrained(
            model_dir,
            device=device,
            batch_size=batch_size,
        )
        image = _png_bytes()
        resolver = MappingAssetResolver(
            {f"benchmark://image/{index}": image for index in range(items)}
        )
    elif backend == "qwen3-vl":
        if not model_dir:
            raise ValueError("--model-dir is required for the qwen3-vl backend")
        encoder = Qwen3VLDenseEncoder.from_pretrained(
            model_dir,
            device=device,
            batch_size=batch_size,
            dimension=dimension,
        )
        image = _png_bytes()
        resolver = MappingAssetResolver(
            {f"benchmark://image/{index}": image for index in range(items)}
        )
    elif backend == "remote":
        if not service_url:
            raise ValueError("--service-url is required for the remote backend")
        if not service_allowed_hosts:
            raise ValueError("--allowed-host is required for the remote backend")
        if service_batch_size is None or service_batch_size <= 0:
            raise ValueError("--service-batch-size is required and must be positive for the remote backend")
        from kogwistar_llm_wiki.multimodal_remote import (
            RemoteMultimodalEncoder,
            RepresentationServiceSettings,
        )
        from llm_wiki_representation_service.config import RepresentationServiceConfig

        capabilities_request = Request(service_url.rstrip("/") + "/v1/capabilities", method="GET")
        if service_token:
            capabilities_request.add_header("Authorization", f"Bearer {service_token}")
        try:
            with urlopen(capabilities_request, timeout=30) as capabilities_response:
                capabilities = json.loads(capabilities_response.read().decode("utf-8"))
        except Exception as exc:
            raise ValueError("unable to inspect remote service capabilities before benchmarking") from exc
        if not isinstance(capabilities, dict) or capabilities.get("batch_size") != service_batch_size:
            actual = capabilities.get("batch_size") if isinstance(capabilities, dict) else None
            raise ValueError(
                f"remote service batch size mismatch: expected {service_batch_size}, got {actual}; "
                "restart the service with LLM_WIKI_REPRESENTATION_BATCH_SIZE"
            )

        profile = RepresentationServiceConfig(
            model=service_model or "Qwen/Qwen3-VL-Embedding-2B",
            revision=service_model_revision,
            dimension=dimension,
            instruction=service_instruction or "Represent the user's input.",
        ).profile
        encoder = RemoteMultimodalEncoder(
            profile,
            RepresentationServiceSettings(
                url=service_url,
                token=service_token,
                allowed_hosts=service_allowed_hosts,
            ),
        )
        image = _png_bytes()
        resolver = MappingAssetResolver(
            {f"benchmark://image/{index}": image for index in range(items)}
        )
    else:
        raise ValueError(f"unsupported benchmark backend {backend!r}")

    cases = (
        ("single_image", _image_units(1)),
        ("image_batch", _image_units(items)),
        ("single_text", _text_units(1)),
        ("text_batch", _text_units(items)),
        ("mixed_image_text_batch", _mixed_units(items)),
    )
    results = [
        _measure_case(
            name,
            units,
            encoder,
            batch_size=batch_size,
            repeats=repeats,
            warmup=warmup,
            resolver=resolver,
        )
        for name, units in cases
    ]
    return {
        "backend": backend,
        "profile": asdict(encoder.profile),
        "batch_size": batch_size,
        "service_batch_size": service_batch_size,
        "requested_items": items,
        "warmup": warmup,
        "cases": [asdict(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("fake", "remote", "qwen3-vl", "colqwen"), default="fake")
    parser.add_argument("--model-dir", help="Local checkpoint directory for a native backend")
    parser.add_argument("--dimension", type=int, default=1024, help="Qwen3-VL MRL output dimension")
    parser.add_argument("--device", choices=("cpu", "cuda"))
    parser.add_argument("--service-url", help="Base URL for the remote representation service")
    parser.add_argument("--service-token", help="Bearer token for the remote representation service")
    parser.add_argument(
        "--service-model",
        help="Service profile model identity; set this when the service uses a local checkpoint path",
    )
    parser.add_argument("--service-model-revision", help="Optional service profile revision")
    parser.add_argument(
        "--service-instruction",
        help="Service profile instruction; defaults to the production instruction",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Explicit allowed service hostname; repeat for multiple hosts",
    )
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--service-batch-size",
        type=int,
        help="Actual remote service microbatch; required for --backend remote and set at service startup",
    )
    parser.add_argument("--items", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run_multimodal_benchmark(
        backend=args.backend,
        model_dir=args.model_dir,
        device=args.device,
        batch_size=args.batch_size,
        items=args.items,
        repeats=args.repeats,
        warmup=args.warmup,
        dimension=args.dimension,
        service_url=args.service_url,
        service_token=args.service_token,
        service_allowed_hosts=tuple(args.allowed_host),
        service_model=args.service_model,
        service_model_revision=args.service_model_revision,
        service_instruction=args.service_instruction,
        service_batch_size=args.service_batch_size,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        from pathlib import Path

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
