from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kogwistar_llm_wiki.email import EmailOntologyBinding


EMAIL_PLUGIN_SRC = Path(__file__).parents[2] / "kogwistar-email-plugin" / "src"
if str(EMAIL_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(EMAIL_PLUGIN_SRC))


def test_email_plugin_ontology_is_validated_by_kogwistar_core() -> None:
    from kogwistar_email_plugin import email_ontology_json

    binding = EmailOntologyBinding.from_plugin(type("Plugin", (), {"email_ontology_json": staticmethod(email_ontology_json)}))

    assert binding.identity["ontology_id"] == "email"
    assert binding.identity["version"] == "1.0.0"


def test_email_ontology_rejects_unknown_mapping_descriptors() -> None:
    from kogwistar_email_plugin import email_ontology_json

    binding = EmailOntologyBinding.from_plugin(type("Plugin", (), {"email_ontology_json": staticmethod(email_ontology_json)}))
    with pytest.raises(ValueError, match="outside the bound ontology"):
        binding.validate_mapping({"entities": [{"class_id": "UntrustedClass"}], "relations": []})
