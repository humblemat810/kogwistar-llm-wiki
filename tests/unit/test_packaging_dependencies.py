from __future__ import annotations

from pathlib import Path

import tomllib


def test_root_package_uses_parser_published_distribution_name() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert "graph-knowledge-doc-parser" in project["project"]["dependencies"]
    assert "kg-doc-parser" not in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["graph-knowledge-doc-parser"] == {
        "path": "kg-doc-parser",
        "editable": True,
    }


def test_dev_extra_keeps_native_multimodal_runtime_explicit() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert all("multimodal" not in requirement for requirement in project["project"]["optional-dependencies"]["dev"])


def test_multimodal_extra_pins_the_tested_portable_torch_generation() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert "torch==2.8.0" in project["project"]["optional-dependencies"]["multimodal"]


def test_named_multimodal_profiles_keep_cuda_index_selection_out_of_pep508() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    assert all(not requirement.startswith("torch") for requirement in extras["multimodal-cpu"])
    assert all(not requirement.startswith("torch") for requirement in extras["multimodal-cuda"])

    cpu_profile = (root / "requirements/multimodal/torch-cpu.txt").read_text(encoding="utf-8")
    assert "https://download.pytorch.org/whl/cpu" in cpu_profile
    assert "torch==2.8.0" in cpu_profile
    assert "torchvision==0.23.0" in cpu_profile

    for name in ("torch-cu126.txt", "torch-cu128.txt"):
        cuda_profile = (root / "requirements/multimodal" / name).read_text(encoding="utf-8")
        assert "torchvision==0.23.0" in cuda_profile


def test_native_multimodal_profiles_include_transformers_device_map_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    for profile in ("multimodal", "multimodal-cpu", "multimodal-cuda"):
        assert any(
            requirement.startswith("accelerate")
            for requirement in extras[profile]
        ), f"{profile} must install accelerate for Transformers device_map support"

    loader = (root / "src/kogwistar_llm_wiki/multimodal_projection.py").read_text(
        encoding="utf-8"
    )
    assert '"device_map"' in loader
    assert "import accelerate" in loader
    assert 'llm_int8_skip_modules=["embedding_proj_layer"]' in loader
