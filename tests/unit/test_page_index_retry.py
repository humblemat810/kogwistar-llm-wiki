from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kg_doc_parser.workflow_ingest import page_index
from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import ProviderEndpointConfig, WorkflowProviderSettings


def _retry_test_settings() -> WorkflowProviderSettings:
    return WorkflowProviderSettings(
        parser=ProviderEndpointConfig(provider="ollama", model="retry-test-model"),
    )


def _sample_text() -> str:
    return (
        "# Retry Doc\n\n"
        "Intro paragraph.\n\n"
        "## First Heading\n"
        "First section body.\n\n"
        "### Detail Heading\n"
        "Nested detail text.\n\n"
        "## Second Heading\n"
        "Second section body.\n"
    )


def _candidate_payload(prompt: str) -> list[dict[str, Any]]:
    marker = "Candidates: "
    payload = prompt.split(marker, 1)[1]
    payload = payload.split("\n\nPrevious attempt failed validation.", 1)[0]
    payload = payload.split("\n\nPrevious structure build failed validation.", 1)[0]
    return json.loads(payload)


def _build_assignments(candidates: list[dict[str, Any]], *, nested: bool) -> list[dict[str, Any]]:
    assignments: list[dict[str, Any]] = []
    last_heading_id: str | None = None
    for candidate in candidates:
        node_type = str(candidate.get("node_type_hint") or candidate.get("node_type") or "PARAGRAPH")
        block_id = str(candidate["block_id"])
        title = str(candidate.get("title_hint") or candidate.get("title") or candidate.get("text") or block_id)
        parent_id: str | None = None
        if nested and node_type in {"SECTION", "SUBSECTION"}:
            parent_id = last_heading_id
            last_heading_id = block_id
        elif nested:
            parent_id = last_heading_id
        assignments.append(
            {
                "block_id": block_id,
                "parent_id": parent_id,
                "node_type": node_type,
                "title": title,
            }
        )
    return assignments


@dataclass
class _FakeStructuredResponse:
    schema: Any
    model: "_RetryAwareChatModel"

    def invoke(self, messages, config=None):
        prompt = str(messages[-1].content)
        self.model.prompts.append(prompt)
        nested = self.model.should_nest(prompt)
        candidates = _candidate_payload(prompt)
        assignments = _build_assignments(candidates, nested=nested)
        parsed = self.schema.model_validate({"assignments": assignments})
        return {"parsed": parsed, "raw": {"prompt": prompt}, "parsing_error": None}


class _RetryAwareChatModel:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.prompts: list[str] = []

    def should_nest(self, prompt: str) -> bool:
        retry_prompt = "Previous attempt failed validation." in prompt
        structure_retry_prompt = "Previous structure build failed validation." in prompt
        if self.mode == "valid":
            return True
        if self.mode == "flat_then_retry":
            return retry_prompt
        if self.mode == "flat_only":
            return False
        if self.mode == "structure_retry":
            return True
        if self.mode == "structure_retry_invalid":
            return not structure_retry_prompt
        return retry_prompt

    def with_structured_output(self, schema, include_raw: bool = True):
        return _FakeStructuredResponse(schema=schema, model=self)


def _make_parser(mode: str, monkeypatch):
    chat = _RetryAwareChatModel(mode)

    def fake_build_chat_model_for_role(role: str, provider_settings):
        assert role == "parser"
        return chat

    monkeypatch.setattr(
        "kg_doc_parser.workflow_ingest.page_index.build_chat_model_for_role",
        fake_build_chat_model_for_role,
    )
    return chat


def test_page_index_llm_retry_succeeds_after_first_heading_flatten(monkeypatch):
    chat = _make_parser("flat_then_retry", monkeypatch)
    result = parse_page_index_document(
        document_id="doc-1",
        title="Retry Doc",
        raw_text=_sample_text(),
        source_format="markdown",
        mode="ollama",
        provider_settings=_retry_test_settings(),
    )

    diag = result.diagnostics
    assert diag["assignment_mode"] == "llm_flat_assignment_retry"
    assert diag["assignment_attempt_count"] == 2
    assert diag["assignment_retry_used"] is True
    assert diag["assignment_retry_succeeded"] is True
    assert diag["structure_retry_used"] is False
    assert diag["structure_retry_succeeded"] is False
    assert diag["retry_used"] is True
    assert diag["retry_succeeded"] is True
    assert diag["assignment_validation_errors"] == ["heading structure was flattened"]
    assert diag["structure_validation_errors"] == []
    assert diag["first_validation_errors"]
    assert diag["retry_validation_errors"] == []
    assert len(chat.prompts) == 2
    assert "heading structure was flattened" in chat.prompts[1]
    assert "Previous attempt failed validation." in chat.prompts[1]
    assert "Fix guidance:" in chat.prompts[1]


def test_page_index_llm_first_pass_success_skips_retry(monkeypatch):
    chat = _make_parser("valid", monkeypatch)
    result = parse_page_index_document(
        document_id="doc-2",
        title="Retry Doc",
        raw_text=_sample_text(),
        source_format="markdown",
        mode="ollama",
        provider_settings=_retry_test_settings(),
    )

    diag = result.diagnostics
    assert diag["assignment_mode"] == "llm_flat_assignment"
    assert diag["final_outcome"] == "first_pass_success"
    assert diag["assignment_attempt_count"] == 1
    assert diag["assignment_retry_used"] is False
    assert diag["assignment_retry_succeeded"] is False
    assert diag["structure_retry_used"] is False
    assert diag["structure_retry_succeeded"] is False
    assert diag["retry_used"] is False
    assert diag["retry_succeeded"] is False
    assert diag["assignment_validation_errors"] == []
    assert diag["structure_validation_errors"] == []
    assert diag["first_validation_errors"] == []
    assert diag["retry_validation_errors"] == []
    assert len(chat.prompts) == 1


def test_page_index_llm_retry_falls_back_after_second_flatten(monkeypatch):
    chat = _make_parser("flat_only", monkeypatch)
    result = parse_page_index_document(
        document_id="doc-3",
        title="Retry Doc",
        raw_text=_sample_text(),
        source_format="markdown",
        mode="ollama",
        provider_settings=_retry_test_settings(),
    )

    diag = result.diagnostics
    assert diag["assignment_mode"] == "deterministic_fallback"
    assert diag["assignment_attempt_count"] == 2
    assert diag["assignment_retry_used"] is True
    assert diag["assignment_retry_succeeded"] is False
    assert diag["structure_retry_used"] is False
    assert diag["structure_retry_succeeded"] is False
    assert diag["retry_used"] is True
    assert diag["retry_succeeded"] is False
    assert diag["assignment_validation_errors"]
    assert diag["structure_validation_errors"] == []
    assert diag["first_validation_errors"]
    assert diag["retry_validation_errors"]
    assert len(chat.prompts) == 2
    assert "heading structure was flattened" in chat.prompts[1]


def test_page_index_structure_retry_succeeds_after_tree_validation_failure(monkeypatch):
    chat = _make_parser("structure_retry", monkeypatch)
    original_validator = page_index._validate_page_index_block_structure
    calls = {"count": 0}

    def fake_structure_validator(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return page_index.PageIndexValidationResult(
                valid=False,
                errors=["assembled heading structure was flattened"],
                warnings=[],
                fallback_reason="structure_validation_failed",
            )
        return original_validator(**kwargs)

    monkeypatch.setattr(page_index, "_validate_page_index_block_structure", fake_structure_validator)
    result = parse_page_index_document(
        document_id="doc-4",
        title="Retry Doc",
        raw_text=_sample_text(),
        source_format="markdown",
        mode="ollama",
        provider_settings=_retry_test_settings(),
    )

    diag = result.diagnostics
    assert diag["assignment_mode"] == "llm_flat_assignment_structure_retry"
    assert diag["final_outcome"] == "structure_retry_success"
    assert diag["assignment_attempt_count"] == 2
    assert diag["assignment_retry_used"] is False
    assert diag["assignment_retry_succeeded"] is False
    assert diag["structure_retry_used"] is True
    assert diag["structure_retry_succeeded"] is True
    assert diag["retry_used"] is True
    assert diag["retry_succeeded"] is True
    assert diag["assignment_validation_errors"] == []
    assert diag["structure_validation_errors"] == ["assembled heading structure was flattened"]
    assert len(chat.prompts) == 2
    assert "Previous structure build failed validation." in chat.prompts[1]
    assert "reuse only existing block_id values" in chat.prompts[1]


def test_page_index_structure_retry_falls_back_after_repair_failure(monkeypatch):
    chat = _make_parser("structure_retry_invalid", monkeypatch)
    original_validator = page_index._validate_page_index_block_structure
    calls = {"count": 0}

    def fake_structure_validator(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return page_index.PageIndexValidationResult(
                valid=False,
                errors=["block 1 excerpt is too broad"],
                warnings=[],
                fallback_reason="structure_validation_failed",
            )
        return original_validator(**kwargs)

    monkeypatch.setattr(page_index, "_validate_page_index_block_structure", fake_structure_validator)
    result = parse_page_index_document(
        document_id="doc-5",
        title="Retry Doc",
        raw_text=_sample_text(),
        source_format="markdown",
        mode="ollama",
        provider_settings=_retry_test_settings(),
    )

    diag = result.diagnostics
    assert diag["assignment_mode"] == "deterministic_fallback"
    assert diag["final_outcome"] == "deterministic_fallback"
    assert diag["assignment_attempt_count"] == 2
    assert diag["assignment_retry_used"] is False
    assert diag["assignment_retry_succeeded"] is False
    assert diag["structure_retry_used"] is True
    assert diag["structure_retry_succeeded"] is False
    assert diag["retry_used"] is True
    assert diag["retry_succeeded"] is False
    assert diag["assignment_validation_errors"] == []
    assert diag["structure_validation_errors"]
    assert len(chat.prompts) == 2
    assert "Previous structure build failed validation." in chat.prompts[1]
