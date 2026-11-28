from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def build_native():
    spec = importlib.util.spec_from_file_location(
        "test_build_native_script", Path(__file__).parents[2] / "scripts" / "build_native.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("build_native script was not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extension_names_match_host_contract(build_native) -> None:
    assert build_native.cargo_artifact_name("win32") == "_kogwistar_llm_wiki_rust.dll"
    assert build_native.cargo_artifact_name("darwin") == "lib_kogwistar_llm_wiki_rust.dylib"
    assert build_native.cargo_artifact_name("linux") == "lib_kogwistar_llm_wiki_rust.so"
    assert build_native.python_artifact_name("win32") == "_kogwistar_llm_wiki_rust.pyd"
    assert build_native.python_artifact_name("linux") == "_kogwistar_llm_wiki_rust.so"


def test_install_native_artifact_renames_for_python_import(
    build_native,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target" / "release"
    target.mkdir(parents=True)
    source = target / build_native.cargo_artifact_name("win32")
    source.write_bytes(b"native")
    destination = tmp_path / "package"

    result = build_native.install_native_artifact(
        target,
        "release",
        destination,
        platform_name="win32",
    )

    assert result == destination / "_kogwistar_llm_wiki_rust.pyd"
    assert result.read_bytes() == b"native"


def test_build_native_reports_failed_cargo_process(build_native, monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="cargo failed"),
    )
    with pytest.raises(subprocess.CalledProcessError):
        build_native.build_native(platform_name="linux")
