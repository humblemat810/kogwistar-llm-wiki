from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_pypi_release_workflow_builds_and_verifies_before_publishing() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-pypi.yml").read_text(
        encoding="utf-8"
    )

    assert "tags:\n      - v*" in workflow
    assert "python scripts/verify_release_version.py" in workflow
    assert "python -m build --sdist --wheel" in workflow
    assert "python -m twine check dist/*" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "environment: pypi" in workflow
    assert "packages-dir: dist/" in workflow
    assert "permissions:\n  contents: read\n  id-token: write" in workflow
