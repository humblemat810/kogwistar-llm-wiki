"""Download the legacy ColQwen checkpoint for explicit comparison only.

Qwen3-VL is the default native multimodal route. This legacy checkpoint is
stored in its normal Hugging Face format. Quantization is
applied at load time by ``ColQwenNativeEncoder`` so the original files remain
reproducible and can be verified by their Hugging Face revision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "vidore/colqwen2-v1.0-hf"
# Revision verified by the local checkpoint in this repository's development
# environment. Override explicitly when adopting a newer model revision.
DEFAULT_REVISION = "ddc07d2317c80f75fc742b7362ee9ad1912908f9"
REQUIRED_FILES = ("config.json", "preprocessor_config.json", "model.safetensors")


def verify_checkpoint(local_dir: Path) -> dict[str, Any]:
    """Fail closed unless a complete ColQwen2 Transformers checkpoint exists."""

    incomplete = sorted(local_dir.rglob("*.incomplete")) if local_dir.exists() else []
    if incomplete:
        raise ValueError(f"checkpoint has incomplete files: {', '.join(str(p) for p in incomplete)}")
    missing = [name for name in REQUIRED_FILES if not (local_dir / name).is_file()]
    if missing:
        raise ValueError(f"checkpoint is missing required files: {', '.join(missing)}")
    try:
        config = json.loads((local_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"checkpoint config is not readable: {local_dir / 'config.json'}") from exc
    architectures = config.get("architectures") or []
    if "ColQwen2ForRetrieval" not in architectures:
        raise ValueError("checkpoint config is not a ColQwen2ForRetrieval model")
    dimension = int(config.get("embedding_dim", 0))
    if dimension <= 0:
        raise ValueError("checkpoint config has no positive embedding_dim")
    size = (local_dir / "model.safetensors").stat().st_size
    if size <= 0:
        raise ValueError("checkpoint model.safetensors is empty")
    return {"model_dir": str(local_dir), "embedding_dim": dimension, "model_bytes": size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--local-dir", type=Path, required=True)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Concurrent Hub downloads; 1 is safest for resumable large checkpoints",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify an already downloaded local checkpoint without contacting the Hub",
    )
    args = parser.parse_args()

    if args.verify_only:
        print(json.dumps(verify_checkpoint(args.local_dir), sort_keys=True))
        return 0

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SystemExit(
            "Install the optional dependencies first: "
            "python -m pip install -r requirements/multimodal/torch-cpu.txt "
            "then python -m pip install -e '.[multimodal-cpu]' "
            "(or install a CUDA requirements/multimodal profile on NVIDIA)"
        ) from exc

    local_dir = snapshot_download(
        repo_id=args.model_id,
        revision=args.revision,
        local_dir=str(args.local_dir),
        max_workers=max(1, args.max_workers),
    )
    print(json.dumps(verify_checkpoint(Path(local_dir)), sort_keys=True))
    print(f"Downloaded {args.model_id} to {local_dir}")
    print("Use ColQwenNativeEncoder.from_pretrained(..., load_in_4bit=True) to load it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
