from __future__ import annotations

import importlib.util
import subprocess
import tomllib
from pathlib import Path

import pytest


class _FakeVersionInfo(tuple):
    major = 3
    minor = 12


def _load_checker():
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_pypy_profile.py"
    spec = importlib.util.spec_from_file_location("check_pypy_profile", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load PyPy profile checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.ci
def test_pypy_report_is_json_serializable_and_rejects_current_cpython() -> None:
    checker = _load_checker()
    report = checker.build_report(required_imports=(), checkout_root=Path.cwd())

    assert report["implementation"] != "PyPy"
    assert not report["ok"]
    assert any("interpreter is not PyPy" in failure for failure in report["failures"])


@pytest.mark.ci
def test_pypy_report_detects_forbidden_modules(monkeypatch, tmp_path: Path) -> None:
    checker = _load_checker()
    monkeypatch.setattr(checker.platform, "python_implementation", lambda: "PyPy")
    monkeypatch.setattr(checker, "sys", type("FakeSys", (), {
        "version_info": _FakeVersionInfo((3, 12)),
        "executable": "pypy3",
    })())
    monkeypatch.setattr(checker, "_module_origin", lambda name: "/env/site-packages/numpy" if name == "numpy" else None)

    report = checker.build_report(required_imports=(), checkout_root=tmp_path)

    assert not report["ok"]
    assert report["forbidden_imports"]["numpy"] == "/env/site-packages/numpy"
    assert any("forbidden module is importable: numpy" in failure for failure in report["failures"])


@pytest.mark.ci
def test_bounded_pypy_requirements_exclude_gpu_and_numpy_profiles() -> None:
    requirements_path = Path(__file__).parents[2] / "requirements" / "pypy-3.12-beta.txt"
    requirements = "\n".join(
        line.split("#", 1)[0]
        for line in requirements_path.read_text(encoding="utf-8").lower().splitlines()
    )
    for forbidden in ("numpy", "chromadb", "pgvector", "torch", "pikepdf"):
        assert forbidden not in requirements


@pytest.mark.ci
def test_pinned_parser_metadata_supports_python_312() -> None:
    parser_pyproject = Path(__file__).parents[2] / "kg-doc-parser" / "pyproject.toml"
    metadata = tomllib.loads(parser_pyproject.read_text(encoding="utf-8"))
    dependencies = metadata["tool"]["poetry"]["dependencies"]

    assert dependencies["python"] == "^3.12"
    assert "platform_python_implementation != 'PyPy'" in dependencies["pikepdf"]["markers"]


@pytest.mark.ci
def test_parser_pins_the_checked_out_kogwistar_revision() -> None:
    root = Path(__file__).parents[2]
    parser_metadata = tomllib.loads(
        (root / "kg-doc-parser" / "pyproject.toml").read_text(encoding="utf-8")
    )
    parser_pin = parser_metadata["tool"]["poetry"]["dependencies"]["kogwistar"]["rev"]
    core_revision = subprocess.check_output(
        ["git", "-C", str(root / "kogwistar"), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    assert parser_pin == core_revision


@pytest.mark.ci
def test_pypy_workflow_has_opt_in_installed_wheel_probe() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "pypy-beta.yml").read_text(
        encoding="utf-8"
    )

    assert "installed_wheel:" in workflow
    assert "--installed-only" in workflow
    assert "python -m pip wheel --no-deps" in workflow
