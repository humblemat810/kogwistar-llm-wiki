from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.ci


def _launch_document() -> dict[str, object]:
    path = Path(__file__).parents[2] / ".vscode" / "launch.json"

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise AssertionError(f"duplicate launch.json key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)


def _configurations() -> list[dict[str, object]]:
    document = _launch_document()
    return [item for item in document["configurations"] if isinstance(item, dict)]


def test_fair_maintenance_launches_use_isolated_persistent_fingerprint_storage() -> None:
    launches = [item for item in _configurations() if "Fair Maintenance First" in str(item["name"])]

    assert {str(item["name"]).rsplit(" — ", 1)[-1] for item in launches} == {
        "Ollama (3 docs)",
        "Azure OpenAI (3 docs)",
    }
    for item in launches:
        env = item["env"]
        assert isinstance(env, dict)
        assert env["KOGWISTAR_LONGRUN_OPERATION_MODE"] == "maintenance_first"
        assert env["KOGWISTAR_LONGRUN_PG_SOURCE"] == "persistent"
        assert env["KOGWISTAR_LONGRUN_PG_DATABASE_MODE"] == "fingerprint"
        assert env["KOGWISTAR_LONGRUN_MAINTENANCE_SCHEDULE"] == "fair_slices"


def test_fair_maintenance_provider_launches_use_matching_model_inputs() -> None:
    launches = {
        str(item["name"]): item
        for item in _configurations()
        if "Fair Maintenance First" in str(item["name"])
    }

    ollama = launches["Longrun: PgVector Fair Maintenance First — Ollama (3 docs)"]["env"]
    azure = launches["Longrun: PgVector Fair Maintenance First — Azure OpenAI (3 docs)"]["env"]
    assert ollama["KOGWISTAR_LONGRUN_PARSER_PROVIDER"] == "ollama"
    assert ollama["KOGWISTAR_LONGRUN_PARSER_MODEL"] == "${input:longrunOllamaModel}"
    assert azure["KOGWISTAR_LONGRUN_PARSER_PROVIDER"] == "azure_openai"
    assert azure["KOGWISTAR_LONGRUN_PARSER_MODEL"] == "${input:longrunAzureModel}"


def test_postgres_dsn_prompt_default_matches_persistent_dev_container() -> None:
    inputs = _launch_document()["inputs"]
    by_id = {item["id"]: item for item in inputs if isinstance(item, dict)}
    expected = "postgresql+psycopg://postgres:postgres@127.0.0.1:35432/postgres"
    assert by_id["longrunPgDsn"]["default"] == expected
    assert by_id["longrunPgCustomDsn"]["default"] == expected
