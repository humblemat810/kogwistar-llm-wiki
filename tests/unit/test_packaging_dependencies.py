from __future__ import annotations

import re
import tomllib
from pathlib import Path


def test_root_package_uses_parser_published_distribution_name() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    dependencies = project["project"]["dependencies"]
    assert any(
        requirement.startswith("graph-knowledge-doc-parser")
        for requirement in dependencies
    )
    assert "graph-knowledge-doc-parser>=0.2.17,<0.3" in dependencies
    assert "kg-doc-parser" not in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["graph-knowledge-doc-parser"] == {
        "path": "kg-doc-parser",
        "editable": True,
    }


def test_all_parser_checkout_workflows_use_the_published_parser_revision() -> None:
    root = Path(__file__).resolve().parents[2]
    expected_revision = "76cdc4f2595a4425523825d05abb0a0fe67cdd85"
    old_revision = "d59f7ad5044df8c059f22539f1fef973a713d531"
    parser_workflows = []

    for workflow_path in sorted((root / ".github" / "workflows").glob("*.yml")):
        content = workflow_path.read_text(encoding="utf-8")
        if "KG_DOC_PARSER_REVISION" not in content:
            continue
        parser_workflows.append(workflow_path.name)
        revisions = re.findall(r"KG_DOC_PARSER_REVISION:\s*([0-9a-f]{40})", content)
        assert revisions, f"{workflow_path} declares no parser revision"
        assert set(revisions) == {expected_revision}, workflow_path
        assert old_revision not in content, workflow_path

    assert parser_workflows == [
        "ci-full.yml",
        "ci.yml",
        "publish-dockerhub-main.yml",
        "publish-dockerhub.yml",
        "publish-pypy311-native-dockerhub.yml",
        "pypy-311-ci-container.yml",
        "pypy-beta.yml",
        "slots-benchmark.yml",
    ]


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

    loader = (root / "src/kogwistar_llm_wiki/embeddings/multimodal_projection.py").read_text(
        encoding="utf-8"
    )
    assert '"device_map"' in loader
    assert "import accelerate" in loader
    assert 'llm_int8_skip_modules=["embedding_proj_layer"]' in loader
