"""Run the same pytest resource slice under several interpreters on one host."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from compare_resource_reports import render
except ModuleNotFoundError:  # Loaded from a repository test via importlib.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from compare_resource_reports import render


def _parse_runtime(value: str) -> tuple[str, Path]:
    try:
        name, executable = value.split("|", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("runtime must be NAME|INTERPRETER_PATH") from exc
    if not name or not executable:
        raise argparse.ArgumentTypeError("runtime name and interpreter path are required")
    return name, Path(executable)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _run_runtime(
    *,
    root: Path,
    runtime: tuple[str, Path],
    pytest_args: list[str],
    output_dir: Path,
) -> tuple[str, Path, int, list[str]]:
    name, executable = runtime
    if not executable.exists():
        raise FileNotFoundError(f"interpreter does not exist: {executable}")
    report_path = output_dir / f"resource-report-{_safe_name(name)}.json"
    command = [
        str(executable),
        "-m",
        "pytest",
        *pytest_args,
        "--resource-report",
        "--resource-report-json",
        str(report_path),
    ]
    env = os.environ.copy()
    import_paths = [root / "kogwistar", root / "kg-doc-parser", root / "kogwistar-obsidian-sink", root / "src"]
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath = os.pathsep.join(str(path) for path in import_paths)
    if existing_pythonpath:
        pythonpath = os.pathsep.join((pythonpath, existing_pythonpath))
    env["PYTHONPATH"] = pythonpath
    print(f"[{name}] {' '.join(command)}")
    completed = subprocess.run(command, cwd=root, env=env, check=False, text=True)
    return name, report_path, completed.returncode, command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        action="append",
        required=True,
        type=_parse_runtime,
        metavar="NAME|INTERPRETER_PATH",
        help="Repeat for each interpreter; all must run in the same host environment.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        dest="pytest_args",
        help="Argument passed to pytest; repeat for each argument.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    pytest_args = args.pytest_args or ["tests", "-q", "-p", "no:cacheprovider"]
    root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    reports: list[tuple[str, dict[str, Any]]] = []
    failures: list[str] = []
    manifest_entries: list[dict[str, object]] = []
    for runtime in args.runtime:
        name, report_path, returncode, command = _run_runtime(
            root=root,
            runtime=runtime,
            pytest_args=pytest_args,
            output_dir=output_dir,
        )
        manifest_entries.append(
            {
                "name": name,
                "interpreter": str(runtime[1]),
                "returncode": returncode,
                "command": command,
                "report": str(report_path),
            }
        )
        if returncode != 0:
            failures.append(f"{name} pytest exited with code {returncode}")
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            reports.append((name, report))
        else:
            failures.append(f"{name} did not produce {report_path}")

    if reports:
        try:
            comparison = render(reports)
        except ValueError as exc:
            failures.append(str(exc))
        else:
            comparison_path = output_dir / "comparison.md"
            comparison_path.write_text(comparison, encoding="utf-8")
            print(comparison, end="")
    manifest = {
        "pytest_args": pytest_args,
        "reports": manifest_entries,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if failures:
        for failure in failures:
            print(f"resource benchmark: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
