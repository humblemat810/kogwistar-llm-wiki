from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_hermes_portable_plugin_manifest_and_mcp_config_are_valid():
    plugin = json.loads((ROOT / "integrations/hermes-agent/plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((ROOT / "integrations/hermes-agent/mcp.json").read_text(encoding="utf-8"))
    assert plugin["manifest_version"] == "1.0.0"
    assert plugin["name"] == "llm-wiki"
    assert "llm-wiki" in mcp["mcpServers"]
    assert mcp["mcpServers"]["llm-wiki"]["command"] == "llm-wiki"


def test_portable_and_canonical_skills_preserve_same_safety_contract():
    canonical = (ROOT / "skills/llm-wiki-knowledge/SKILL.md").read_text(encoding="utf-8")
    hermes = (ROOT / "integrations/hermes-agent/skills/llm-wiki/SKILL.md").read_text(encoding="utf-8")
    for text in (canonical, hermes):
        assert "no_change" in text
        assert "explicit confirmation" in text
        assert "Never invent" in text
