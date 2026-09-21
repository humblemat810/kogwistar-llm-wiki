"""Run slot benchmarks under several interpreters on one host.

Runtime numbers are only compared when every child reports the same host
environment. Use ``name|interpreter|warmup`` entries so Windows paths can
contain a drive-letter colon without becoming ambiguous.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path


def _parse_runtime(value: str) -> tuple[str, Path, int]:
    try:
        name, executable, warmup = value.split("|", 2)
        parsed_warmup = int(warmup)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "runtime must be NAME|INTERPRETER_PATH|WARMUP_COUNT"
        ) from exc
    if not name or not executable:
        raise argparse.ArgumentTypeError("runtime name and interpreter path are required")
    if parsed_warmup < 0:
        raise argparse.ArgumentTypeError("warm-up count must not be negative")
    return name, Path(executable), parsed_warmup


def _run_runtime(
    *,
    script: Path,
    runtime: tuple[str, Path, int],
    counts: tuple[int, ...],
    output_dir: Path,
) -> dict[str, object]:
    name, executable, warmup = runtime
    if not executable.exists():
        raise FileNotFoundError(f"interpreter does not exist: {executable}")
    output = output_dir / f"{name}.json"
    command = [str(executable), str(script), "--warmup-count", str(warmup)]
    for count in counts:
        command.extend(("--count", str(count)))
    command.extend(("--json-out", str(output)))
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise RuntimeError(f"benchmark failed for {name} with exit code {completed.returncode}")
    report = json.loads(output.read_text(encoding="utf-8"))
    report["runner_name"] = name
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        action="append",
        required=True,
        type=_parse_runtime,
        metavar="NAME|INTERPRETER|WARMUP",
        help="Repeat for each interpreter; all must run in the same host environment.",
    )
    parser.add_argument("--count", action="append", type=int, dest="counts")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    counts = tuple(args.counts or (1000, 10000))
    if any(count <= 0 for count in counts):
        parser.error("--count values must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("benchmark_slots.py")
    reports = [
        _run_runtime(script=script, runtime=runtime, counts=counts, output_dir=args.output_dir)
        for runtime in args.runtime
    ]
    environments = {str(report["host_environment"]) for report in reports}
    if len(environments) != 1:
        raise RuntimeError(f"mixed host environments are not comparable: {sorted(environments)}")
    manifest = {
        "host_environment": next(iter(environments)),
        "host_system": platform.system(),
        "host_release": platform.release(),
        "host_machine": platform.machine(),
        "counts": counts,
        "reports": reports,
    }
    manifest_path = args.manifest or args.output_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
