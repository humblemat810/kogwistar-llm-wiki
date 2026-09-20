from __future__ import annotations

import ast
import importlib.util
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

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


def _load_slot_runner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_same_host_benchmarks.py"
    spec = importlib.util.spec_from_file_location("run_same_host_benchmarks", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load same-host benchmark runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_resource_runner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_same_host_resource_reports.py"
    spec = importlib.util.spec_from_file_location("run_same_host_resource_reports", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load same-host resource runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_slot_benchmark():
    path = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_slots.py"
    spec = importlib.util.spec_from_file_location("benchmark_slots", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load slot benchmark")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.ci
def test_pypy_report_is_json_serializable_and_rejects_current_cpython() -> None:
    checker = _load_checker()
    report = checker.build_report(required_imports=(), checkout_root=Path.cwd())

    if checker.platform.python_implementation() == "PyPy":
        assert report["implementation"] == "PyPy"
        assert isinstance(report["failures"], list)
        return

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
def test_pypy_report_accepts_an_explicit_experimental_python_version(monkeypatch, tmp_path: Path) -> None:
    checker = _load_checker()
    monkeypatch.setattr(checker.platform, "python_implementation", lambda: "PyPy")
    monkeypatch.setattr(checker, "sys", type("FakeSys", (), {
        "version_info": _FakeVersionInfo((3, 11)),
        "executable": "pypy3",
    })())
    monkeypatch.setattr(checker, "_module_origin", lambda _name: None)

    report = checker.build_report(
        required_imports=(),
        checkout_root=tmp_path,
        expected_python=(3, 11),
    )

    assert report["ok"] is True


@pytest.mark.ci
def test_pypy_report_records_crash_sensitive_import_failures(monkeypatch, tmp_path: Path) -> None:
    checker = _load_checker()
    monkeypatch.setattr(checker.platform, "python_implementation", lambda: "PyPy")
    monkeypatch.setattr(checker, "sys", type("FakeSys", (), {
        "version_info": _FakeVersionInfo((3, 11)),
        "executable": "pypy3",
    })())
    monkeypatch.setattr(checker, "_module_origin", lambda _name: None)
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=-11,
            stdout="",
            stderr="segmentation fault",
        ),
    )

    report = checker.build_report(
        required_imports=(),
        checkout_root=tmp_path,
        expected_python=(3, 11),
        import_probes=("mcp.server.lowlevel",),
    )

    probe = report["import_probes"]["mcp.server.lowlevel"]
    assert probe["ok"] is False
    assert probe["returncode"] == -11
    assert any("import probe failed: mcp.server.lowlevel" in failure for failure in report["failures"])


@pytest.mark.ci
def test_pypy_311_profile_is_separate_and_non_numpy() -> None:
    requirements_path = Path(__file__).parents[2] / "requirements" / "pypy-3.11-experimental.txt"
    requirements = "\n".join(
        line.split("#", 1)[0]
        for line in requirements_path.read_text(encoding="utf-8").lower().splitlines()
    )

    for forbidden in ("numpy", "chromadb", "pgvector", "torch", "pikepdf"):
        assert forbidden not in requirements

    for provider_package in ("langchain-openai", "langchain-google-genai", "langchain-ollama"):
        assert provider_package not in requirements


@pytest.mark.ci
def test_bounded_pypy_profiles_exclude_optional_provider_adapters() -> None:
    root = Path(__file__).parents[2]
    for profile_name in ("pypy-3.11-experimental.txt", "pypy-3.12-beta.txt"):
        requirements = (root / "requirements" / profile_name).read_text(
            encoding="utf-8"
        ).lower()
        for provider_package in ("langchain-openai", "langchain-google-genai", "langchain-ollama"):
            assert provider_package not in requirements


@pytest.mark.ci
def test_usage_contract_does_not_import_optional_provider_at_collection_time() -> None:
    """Provider-free lanes must collect without an Azure SDK installed."""
    path = Path(__file__).parents[2] / "tests" / "unit" / "test_llm_usage.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    top_level_provider_imports = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            (isinstance(node, ast.Import) and any(alias.name == "langchain_openai" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "langchain_openai")
        )
    ]
    assert not top_level_provider_imports


@pytest.mark.ci
def test_windows_pywin32_gap_does_not_block_the_linux_pypy_profile() -> None:
    root = Path(__file__).parents[2]
    requirements = (root / "requirements" / "pypy-3.11-experimental.txt").read_text(
        encoding="utf-8"
    ).lower()
    workflow = (root / ".github" / "workflows" / "pypy-311-experimental.yml").read_text(
        encoding="utf-8"
    )
    docs = (root / "doc" / "pypy_3_12_support_action_plan.md").read_text(
        encoding="utf-8"
    )

    assert "pywin32" not in requirements
    assert "runs-on: ubuntu-latest" in workflow
    assert "container-image blocker" in docs


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
def test_pypy_beta_profile_pins_only_ci_compatibility_workarounds() -> None:
    constraints_path = Path(__file__).parents[2] / "kogwistar" / "constraints-pypy-3.12.txt"
    constraints = constraints_path.read_text(encoding="utf-8")

    assert "rpds-py==2026.5.1" in constraints
    assert "pydantic==2.12.5" in constraints
    assert "pydantic-core==2.41.5" in constraints
    assert "numpy" not in constraints.lower()
    assert "chromadb" not in constraints.lower()


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
    lock_packages = tomllib.loads(
        (root / "kg-doc-parser" / "poetry.lock").read_text(encoding="utf-8")
    )["package"]
    lock_pin = next(
        package["source"]["resolved_reference"]
        for package in lock_packages
        if package["name"] == "kogwistar"
    )
    core_revision = subprocess.check_output(
        ["git", "-C", str(root / "kogwistar"), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    parser_ci_workflow = (root / "kg-doc-parser" / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert parser_pin == core_revision
    assert lock_pin == core_revision
    assert f"ref: {core_revision}" in parser_ci_workflow


@pytest.mark.ci
def test_pypy_workflow_has_opt_in_installed_wheel_probe() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "pypy-beta.yml").read_text(
        encoding="utf-8"
    )

    assert "installed_wheel:" in workflow
    assert "pypy3.12-v8.0.0-linux64.tar.gz" in workflow
    assert "pypy-c-jit-171509-4db12e5e6f4d-linux64.tar.gz" not in workflow
    assert "a1b4851459c2b3dffccab71cb08989534fab0839deddd34f0e6bf18add256fd7" in workflow
    assert "PYPY_SHA256" in workflow
    assert "actual_sha256" in workflow
    assert "constraints-pypy-3.12.txt" in workflow
    assert "actions/setup-python@v5" not in workflow
    assert "--installed-only" in workflow
    assert "python -m pip wheel --no-deps" in workflow
    assert workflow.count("--probe-import mcp.server.lowlevel") == 2
    assert "pypy-profile-pypy312.json" in workflow
    assert "pypy-installed-profile-pypy312.json" in workflow
    assert "pip-freeze-pypy312.txt" in workflow
    assert "pip-install-pypy312.log" in workflow
    assert "pip-install-status-pypy312.txt" in workflow
    assert "provider-sdk-status-pypy312.txt" in workflow
    assert "pip-resolver-status-pypy312.txt" in workflow
    assert "Capture PyPy beta resolver evidence" in workflow
    assert "if: always()" in workflow


@pytest.mark.ci
def test_pypy312_archive_defaults_are_real_and_consistent() -> None:
    root = Path(__file__).parents[2]
    expected_url = "https://buildbot.pypy.org/pypy/pypy3.12-v8.0.0-linux64.tar.gz"
    expected_sha256 = "a1b4851459c2b3dffccab71cb08989534fab0839deddd34f0e6bf18add256fd7"
    old_wrong_archive = "pypy-c-jit-171509-4db12e5e6f4d-linux64.tar.gz"

    for relative_path in (
        Path(".github/workflows/pypy-beta.yml"),
        Path(".github/workflows/publish-pypy-ci-dockerhub.yml"),
        Path("Dockerfile.pypy-ci"),
    ):
        content = (root / relative_path).read_text(encoding="utf-8")
        assert expected_url in content
        assert expected_sha256 in content
        assert old_wrong_archive not in content


@pytest.mark.ci
def test_pypy_311_workflow_is_pinned_nonblocking_and_python_authority_only() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "pypy-311-experimental.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    runner = (Path(__file__).parents[2] / "scripts" / "run_pypy311_ci.py").read_text(
        encoding="utf-8"
    )

    assert "continue-on-error: true" in workflow
    assert "uses: actions/setup-python@v7" in workflow
    assert "python-version: pypy-3.11-v7.3.20" in workflow
    assert "cache: pip" in workflow
    assert "Install official PyPy 3.11 release" not in workflow
    assert "pypy_url" not in workflow
    assert "pypy_sha256" not in workflow
    assert '"--expected-python"' in runner
    assert '"3.11"' in runner
    assert 'env.setdefault("KOGWISTAR_IMPL_MODE", "python")' in runner
    assert "pypy-3.11-experimental.txt" in workflow
    assert '"mcp.server.lowlevel"' in runner
    assert "pypy-profile-pypy311.json" in workflow
    assert "pip-freeze-pypy311.txt" in workflow
    assert "pip-resolver-status-pypy311.txt" in workflow
    assert "pip_check_exit_code" in workflow
    assert "source-profile metadata gaps" in workflow
    assert "Upload PyPy 3.11 profile evidence" in workflow
    assert "does not publish an image" in workflow


@pytest.mark.ci
def test_pypy_311_workflow_publishes_failed_pytest_diagnostics() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "pypy-311-experimental.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    runner = (Path(__file__).parents[2] / "scripts" / "run_pypy311_ci.py").read_text(
        encoding="utf-8"
    )

    assert 'pytest_log = results / "pytest-pypy311.log"' in runner
    assert "log_path.open" in runner
    assert "pytest-log-pypy311" in workflow
    assert "::error title=PyPy 3.11 compatibility profile failure::" in workflow
    assert "provider-sdk-status-pypy311.txt" in workflow


@pytest.mark.ci
def test_pypy_311_base_requirements_use_the_official_mcp_sdk() -> None:
    requirements = (
        Path(__file__).parents[2] / "requirements" / "pypy-3.11-experimental.txt"
    ).read_text(encoding="utf-8")
    assert "mcp>=1.27,<2" in requirements
    for provider_package in ("langchain-openai", "langchain-google-genai", "langchain-ollama"):
        assert provider_package not in requirements
    assert "fastmcp" not in requirements.lower()


@pytest.mark.ci
def test_pypy_311_workflow_probes_the_official_mcp_sdk() -> None:
    workflow = (
        Path(__file__).parents[2]
        / ".github"
        / "workflows"
        / "pypy-311-experimental.yml"
    ).read_text(encoding="utf-8")
    runner = (Path(__file__).parents[2] / "scripts" / "run_pypy311_ci.py").read_text(
        encoding="utf-8"
    )

    assert '"mcp.server.lowlevel"' in runner
    assert "fastmcp" not in workflow.lower()


@pytest.mark.ci
def test_shared_pypy_runner_owns_the_native_profile_selection() -> None:
    runner = (Path(__file__).parents[2] / "scripts" / "run_pypy311_ci.py").read_text(
        encoding="utf-8"
    )

    assert "sys.implementation.name != \"pypy\"" in runner
    assert "sys.version_info[:2] != (3, 11)" in runner
    assert "mcp.server.lowlevel" in runner
    assert "--resource-report-json" in runner
    assert "not ci_full and not slow and not manual" in runner


@pytest.mark.ci
def test_slots_benchmark_workflow_covers_cpython_and_pypy_with_artifacts() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "slots-benchmark.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "runtime: cpython" in workflow
    assert 'version: "3.12"' in workflow
    assert 'version: "3.13"' in workflow
    assert 'version: "3.14"' in workflow
    assert "runtime: pypy311" in workflow
    assert "python-version: pypy-3.11-v7.3.20" in workflow
    assert "--warmup-count" in workflow
    assert "--count 1000" in workflow
    assert "--count 10000" in workflow
    assert "-e ./kogwistar" not in workflow
    benchmark_script = Path(
        __file__
    ).parents[2].joinpath("scripts", "benchmark_slots.py").read_text(encoding="utf-8")
    assert "cpu_microseconds_per_instance" in benchmark_script
    assert "cpu_utilization_percent" in benchmark_script
    assert "actions/upload-artifact@v6" in workflow
    assert "continue-on-error: true" in workflow


@pytest.mark.ci
def test_same_host_benchmark_runner_rejects_ambiguous_runtime_specs() -> None:
    runner = _load_slot_runner()

    assert runner._parse_runtime("pypy|C:/tools/pypy3.exe|1000") == (
        "pypy",
        Path("C:/tools/pypy3.exe"),
        1000,
    )
    with pytest.raises(Exception, match="NAME\\|INTERPRETER_PATH\\|WARMUP_COUNT"):
        runner._parse_runtime("pypy-only")


@pytest.mark.ci
def test_same_host_resource_runner_rejects_ambiguous_runtime_specs() -> None:
    runner = _load_resource_runner()

    assert runner._parse_runtime("pypy|C:/tools/pypy3.exe") == (
        "pypy",
        Path("C:/tools/pypy3.exe"),
    )
    with pytest.raises(Exception, match="NAME\\|INTERPRETER_PATH"):
        runner._parse_runtime("pypy-only")


@pytest.mark.ci
def test_slot_benchmark_records_host_boundary_and_same_host_runner() -> None:
    root = Path(__file__).parents[2]
    benchmark = (root / "scripts" / "benchmark_slots.py").read_text(encoding="utf-8")
    runner = (root / "scripts" / "run_same_host_benchmarks.py").read_text(encoding="utf-8")

    assert '"host_environment"' in benchmark
    assert '"python_executable"' in benchmark
    assert "mixed host environments are not comparable" in runner
    assert "WSL" in (root / "doc" / "testing_guide.md").read_text(encoding="utf-8")
    resource_runner = (root / "scripts" / "run_same_host_resource_reports.py").read_text(
        encoding="utf-8"
    )
    assert "--resource-report" in resource_runner
    assert "comparison.md" in resource_runner


@pytest.mark.ci
def test_slot_benchmark_reports_stale_optional_vendor_layouts(monkeypatch) -> None:
    benchmark = _load_slot_benchmark()

    @dataclass(frozen=True)
    class LegacyVendorRecord:
        value: str = "legacy"

    monkeypatch.setattr(benchmark, "_sample_values", lambda: {LegacyVendorRecord: ()})
    monkeypatch.setattr(
        benchmark,
        "_OPTIONAL_VENDOR_SLOT_TYPES",
        frozenset({LegacyVendorRecord}),
    )

    results, skipped = benchmark.run(counts=(1,))

    assert results
    assert "LegacyVendorRecord" in skipped
    assert "advance the dependency pin" in skipped["LegacyVendorRecord"]


@pytest.mark.ci
def test_ci_uploads_per_test_resource_reports_without_making_them_gates() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "--resource-report" in workflow
    assert "--resource-report-json" in workflow
    assert "actions/upload-artifact@v6" in workflow
    assert "resource-report-cpython-${{ matrix.python-version }}" in workflow
    reporter = (Path(__file__).parents[2] / "tests" / "_helpers" / "resource_report.py").read_text(
        encoding="utf-8"
    )
    for field in ("host_environment", "python_executable", "host_machine"):
        assert f'"{field}"' in reporter


@pytest.mark.ci
def test_ci_aggregates_runtime_resource_reports_without_making_comparison_a_gate() -> None:
    root = Path(__file__).parents[2]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    comparison = (root / "scripts" / "compare_resource_reports.py").read_text(encoding="utf-8")

    assert "resource-comparison:" in workflow
    assert "actions/download-artifact@v6" in workflow
    assert "merge-multiple: true" in workflow
    assert "continue-on-error: true" in workflow
    assert "cpython312=" in workflow
    assert "cpython313=" in workflow
    assert "mixed host environments" in comparison


@pytest.mark.ci
def test_root_workflows_use_node24_action_majors() -> None:
    workflow_dir = Path(__file__).parents[2] / ".github" / "workflows"
    workflows = "\n".join(path.read_text(encoding="utf-8") for path in workflow_dir.glob("*.yml"))

    assert "actions/checkout@v4" not in workflows
    assert "actions/setup-python@v5" not in workflows
    assert "actions/upload-artifact@v4" not in workflows


@pytest.mark.ci
def test_downstream_workflows_pin_the_checked_out_vendor_revisions() -> None:
    root = Path(__file__).parents[2]
    expected = {
        "KOGWISTAR_REVISION": subprocess.check_output(
            ["git", "-C", str(root / "kogwistar"), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "KG_DOC_PARSER_REVISION": subprocess.check_output(
            ["git", "-C", str(root / "kg-doc-parser"), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "OBSIDIAN_SINK_REVISION": subprocess.check_output(
            ["git", "-C", str(root / "kogwistar-obsidian-sink"), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
    }
    workflows = tuple((root / ".github" / "workflows").glob("*.yml"))

    for workflow_path in workflows:
        workflow = workflow_path.read_text(encoding="utf-8")
        for name, revision in expected.items():
            if f"{name}:" in workflow:
                assert f"{name}: {revision}" in workflow, workflow_path
