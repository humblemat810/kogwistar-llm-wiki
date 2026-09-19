import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_application_release_workflow_does_not_publish_embedding_images() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-dockerhub.yml").read_text(encoding="utf-8")

    assert "publish-app:" in workflow
    assert "Dockerfile.embedding-service" not in workflow
    assert "kogwistar-llm-wiki-embedding" not in workflow
    assert "cache-from: type=gha,scope=llm-wiki" in workflow


def test_embedding_release_workflow_is_manual_and_explicit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-embedding-dockerhub.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "confirm:" in workflow
    assert "embedding-cpu" in workflow
    assert "embedding-cuda12.8" in workflow
    assert "Dockerfile.embedding-service" in workflow
    assert "promote_latest:" in workflow


def test_pypy_ci_image_release_is_manual_pinned_and_separate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-pypy-ci-dockerhub.yml").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "Dockerfile.pypy-ci").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "confirm:" in workflow
    assert "pypy_sha256:" in workflow
    assert "pypy_url:" in workflow
    assert "kogwistar-llm-wiki-pypy-ci" in workflow
    assert "Dockerfile.pypy-ci" in workflow
    assert "cache-from: type=gha,scope=pypy-ci" in workflow
    assert "docker push" in workflow
    assert "PYPY_SHA256" in dockerfile
    assert "sha256sum --check" in dockerfile
    assert "pypy-c-jit-latest" not in dockerfile
    assert "ENTRYPOINT [\"pypy3\"]" in dockerfile


def test_local_publishers_default_to_application_and_offer_explicit_targets() -> None:
    powershell = (ROOT / "scripts" / "publish_docker_images.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "scripts" / "publish_docker_images.sh").read_text(encoding="utf-8")

    assert '[string]$Target = "app"' in powershell
    assert '"embedding-cpu", "embedding-cuda12.8", "all"' in powershell
    assert 'target="app"' in bash
    assert "embedding-cpu|embedding-cuda12.8|all" in bash
    assert '"buildx", "build", "--load"' in powershell
    assert "docker_args buildx build --load" in bash


def test_release_verifier_matches_package_version_and_rejects_mismatch() -> None:
    script = ROOT / "scripts" / "verify_release_version.py"
    matching = subprocess.run(
        [sys.executable, str(script), "--tag", "v0.4.0"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    mismatching = subprocess.run(
        [sys.executable, str(script), "--tag", "v99.99.99"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert matching.returncode == 0
    assert mismatching.returncode != 0
    assert "does not match" in mismatching.stderr


def test_release_publishers_reject_reuse_and_align_github_tag_names() -> None:
    powershell = (ROOT / "scripts" / "publish_docker_images.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "scripts" / "publish_docker_images.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "publish-dockerhub.yml").read_text(encoding="utf-8")

    assert "AllowExistingTag" in powershell
    assert "--allow-existing-tag" in bash
    assert "Docker tag already exists" in powershell
    assert "Docker tag already exists" in bash
    assert "verify_release_version.py" in workflow
    assert "type=semver,pattern=v{{version}}" in workflow
    assert "Reject an existing release tag" in workflow


def test_main_image_publishes_only_after_successful_ci_to_configured_namespace() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-dockerhub-main.yml").read_text(
        encoding="utf-8"
    )

    assert 'workflows: ["CI"]' in workflow
    assert "types: [completed]" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "DOCKERHUB_USERNAME" in workflow
    assert "DOCKERHUB_TOKEN" in workflow
    assert "DOCKERHUB_SECONDARY_" not in workflow
    assert "type=raw,value=main" in workflow
    assert "type=raw,value=sha-${{ github.event.workflow_run.head_sha }}" in workflow
    assert "Dockerfile.embedding-service" not in workflow


def test_github_workflows_pin_all_checked_out_vendor_revisions() -> None:
    workflow_paths = (
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".github" / "workflows" / "publish-dockerhub.yml",
        ROOT / ".github" / "workflows" / "publish-dockerhub-main.yml",
        ROOT / ".github" / "workflows" / "pypy-beta.yml",
    )
    expected = {
        "KOGWISTAR_REVISION": "kogwistar",
        "KG_DOC_PARSER_REVISION": "kg-doc-parser",
        "OBSIDIAN_SINK_REVISION": "kogwistar-obsidian-sink",
    }
    for variable, directory in expected.items():
        current = subprocess.check_output(
            ["git", "-C", str(ROOT / directory), "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        for path in workflow_paths:
            pins = re.findall(rf"{variable}:\s*([0-9a-f]{{40}})", path.read_text(encoding="utf-8"))
            assert pins
            assert set(pins) == {current}
