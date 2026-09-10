"""Download and verify the recommended Qwen3-VL dense embedding checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-2B"


def verify_checkpoint(local_dir: Path) -> dict[str, Any]:
    """Verify a completed Qwen3-VL snapshot without importing Torch."""
    if not local_dir.is_dir():
        raise ValueError(f"checkpoint directory does not exist: {local_dir}")
    incomplete = sorted(local_dir.rglob("*.incomplete"))
    if incomplete:
        raise ValueError(
            f"checkpoint has incomplete files: {', '.join(str(path) for path in incomplete)}"
        )
    config_path = local_dir / "config.json"
    if not config_path.is_file():
        raise ValueError("checkpoint is missing required files: config.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"checkpoint config is not readable: {config_path}") from exc
    architectures = [str(value) for value in config.get("architectures") or []]
    model_type = str(config.get("model_type", ""))
    if not any("Qwen3VL" in value for value in architectures) and "qwen3_vl" not in model_type:
        raise ValueError("checkpoint config is not a Qwen3-VL checkpoint")
    weight_files = sorted(
        path for path in local_dir.rglob("*")
        if path.is_file() and path.suffix in {".safetensors", ".bin", ".pt"}
    )
    if not weight_files:
        raise ValueError("checkpoint is missing model weight files")
    total_bytes = sum(path.stat().st_size for path in weight_files)
    if total_bytes <= 0:
        raise ValueError("checkpoint model weight files are empty")
    return {
        "model_dir": str(local_dir),
        "model_type": model_type,
        "architectures": architectures,
        "weight_files": len(weight_files),
        "model_bytes": total_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--revision")
    parser.add_argument("--local-dir", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        print(json.dumps(verify_checkpoint(args.local_dir), sort_keys=True))
        return 0
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SystemExit(
            "Install the optional runtime first with "
            "python -m pip install -r requirements/multimodal/torch-cpu.txt "
            "then python -m pip install -e '.[multimodal-cpu]'"
        ) from exc
    local_dir = snapshot_download(
        repo_id=args.model_id,
        revision=args.revision,
        local_dir=str(args.local_dir),
        max_workers=max(1, args.max_workers),
    )
    print(json.dumps(verify_checkpoint(Path(local_dir)), sort_keys=True))
    print(f"Downloaded {args.model_id} to {local_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
