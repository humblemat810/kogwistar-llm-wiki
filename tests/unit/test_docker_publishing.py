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


def test_local_publishers_default_to_application_and_offer_explicit_targets() -> None:
    powershell = (ROOT / "scripts" / "publish_docker_images.ps1").read_text(encoding="utf-8")
    bash = (ROOT / "scripts" / "publish_docker_images.sh").read_text(encoding="utf-8")

    assert '[string]$Target = "app"' in powershell
    assert '"embedding-cpu", "embedding-cuda12.8", "all"' in powershell
    assert 'target="app"' in bash
    assert "embedding-cpu|embedding-cuda12.8|all" in bash
    assert '"buildx", "build", "--load"' in powershell
    assert "docker_args buildx build --load" in bash
