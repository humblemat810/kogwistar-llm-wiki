#!/usr/bin/env python3
"""Verify that a release tag matches the application package version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

RELEASE_TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")
PROJECT_SECTION = re.compile(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)")
PROJECT_VERSION = re.compile(r"(?m)^version\s*=\s*[\"']([^\"']+)[\"']\s*$")


def project_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    project_match = PROJECT_SECTION.search(text)
    version_match = PROJECT_VERSION.search(project_match.group(1)) if project_match else None
    if version_match is None:
        raise TypeError("pyproject.toml must define a static project.version")
    return version_match.group(1)


def verify_release_tag(root: Path, tag: str) -> None:
    if tag == "latest":
        return
    match = RELEASE_TAG.fullmatch(tag)
    if match is None:
        raise ValueError("release tags must use the form vMAJOR.MINOR.PATCH or latest")
    actual = project_version(root)
    expected = match.group("version")
    if actual != expected:
        raise ValueError(
            f"release tag {tag!r} does not match pyproject.toml version {actual!r}; "
            f"bump the package version or use v{actual}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="versioned release tag or latest")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        verify_release_tag(root, args.tag)
        version = project_version(root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"release tag {args.tag} matches package version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
