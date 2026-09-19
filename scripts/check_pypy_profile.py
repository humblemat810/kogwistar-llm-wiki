#!/usr/bin/env python3
"""Validate the experimental NumPy-free PyPy 3.12 application profile.

This is deliberately a probe rather than an installer.  It can run before the
application dependencies are available and gives a precise diagnostic for the
interpreter, forbidden optional dependencies, and installed-package origins.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path


FORBIDDEN_MODULES = ("numpy", "chromadb")
DEFAULT_REQUIRED_IMPORTS: tuple[str, ...] = ()


def _module_origin(name: str) -> str | None:
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ModuleNotFoundError, ValueError):
        return None
    if spec is None:
        return None
    if spec.origin and spec.origin not in {"built-in", "frozen"}:
        return str(Path(spec.origin).resolve())
    locations = spec.submodule_search_locations
    if locations:
        return str(Path(next(iter(locations))).resolve())
    return spec.origin


def _is_under(path: str | None, root: Path) -> bool:
    if not path or path in {"built-in", "frozen"}:
        return False
    try:
        Path(path).resolve().relative_to(root)
    except ValueError:
        return False
    return True


def build_report(
    *,
    required_imports: tuple[str, ...] = (),
    installed_only: bool = False,
    checkout_root: Path | None = None,
) -> dict[str, object]:
    """Return a JSON-serializable compatibility report without importing apps."""

    root = (checkout_root or Path(__file__).resolve().parents[1]).resolve()
    origins = {name: _module_origin(name) for name in (*required_imports, *FORBIDDEN_MODULES)}
    failures: list[str] = []

    if platform.python_implementation() != "PyPy":
        failures.append("interpreter is not PyPy")
    if sys.version_info[:2] != (3, 12):
        failures.append(f"interpreter is Python {sys.version_info[0]}.{sys.version_info[1]}, expected 3.12")

    for name in FORBIDDEN_MODULES:
        if origins[name] is not None:
            failures.append(f"forbidden module is importable: {name} ({origins[name]})")

    if installed_only:
        pythonpath = os.environ.get("PYTHONPATH", "").strip()
        if pythonpath:
            failures.append("PYTHONPATH must be empty for installed-only validation")
        for name in required_imports:
            origin = origins[name]
            if origin is None:
                failures.append(f"required installed package is not importable: {name}")
            elif _is_under(origin, root):
                failures.append(f"required package resolves to the checkout, not an installed wheel: {name} ({origin})")
    else:
        for name in required_imports:
            if origins[name] is None:
                failures.append(f"required package is not importable: {name}")

    return {
        "ok": not failures,
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "required_imports": {name: origins[name] for name in required_imports},
        "forbidden_imports": {name: origins[name] for name in FORBIDDEN_MODULES},
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-import",
        dest="required_imports",
        action="append",
        default=None,
        help="Require a package to be importable; repeat for more than one package.",
    )
    parser.add_argument(
        "--installed-only",
        action="store_true",
        help="Reject PYTHONPATH and package origins inside this checkout.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args()
    required = tuple(args.required_imports or DEFAULT_REQUIRED_IMPORTS)
    report = build_report(required_imports=required, installed_only=args.installed_only)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"implementation={report['implementation']} version={report['version']}")
        print(f"executable={report['executable']}")
        for name, origin in report["required_imports"].items():
            print(f"required {name}: {origin or 'missing'}")
        for name, origin in report["forbidden_imports"].items():
            print(f"forbidden {name}: {origin or 'absent'}")
        if report["failures"]:
            print("FAIL:")
            for failure in report["failures"]:
                print(f"- {failure}")
        else:
            print("PASS: experimental NumPy-free PyPy profile")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
