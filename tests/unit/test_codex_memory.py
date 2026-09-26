import pytest
from kogwistar.engine_core.models import Grounding, Node, Span
from pydantic import ValidationError

from kogwistar_llm_wiki.memory.service import (
    CodexMemoryError,
    CodexMemoryRecord,
    CodexMemoryService,
    MemoryDisabledError,
    MemoryEvidence,
)
from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _evidence(path: str = "src/app.py", *, excerpt: str = "The app uses a bounded memory API") -> dict[str, object]:
    return {
        "kind": "repository",
        "repository_path": path,
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "content_sha256": "a" * 64,
        "start_line": 10,
        "end_line": 12,
        "excerpt": excerpt,
    }


def _record(*, workspace_id: str = "project-a", confidence: str = "verified", statement: str = "Use bounded memory") -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "session_id": "session-1",
        "kind": "convention",
        "statement": statement,
        "confidence": confidence,
        "evidence": [_evidence()],
    }


def test_memory_record_requires_bounded_evidence_and_rejects_secrets():
    with pytest.raises(ValueError, match="at least 1 item|at least one evidence"):
        CodexMemoryRecord(
            workspace_id="project-a",
            session_id="session-1",
            kind="finding",
            statement="A fact without proof",
            confidence="verified",
            evidence=[],
        )
    with pytest.raises(ValueError, match="secret-like"):
        CodexMemoryRecord.model_validate({**_record(), "statement": "password=never-store"})
    with pytest.raises(ValueError, match="drive-qualified"):
        MemoryEvidence.model_validate({**_evidence(), "repository_path": "C:/repo/src/app.py"})
    record = CodexMemoryRecord.model_validate(_record())
    with pytest.raises(ValidationError):
        record.statement = "mutated"


def test_memory_metadata_is_bounded_json_and_preserved():
    record = CodexMemoryRecord.model_validate(
        {
            **_record(),
            "metadata": {"ontology": {"ontology_id": "email"}, "stream_ids": ["stream-a"]},
        }
    )
    assert record.metadata == {
        "ontology": {"ontology_id": "email"},
        "stream_ids": ["stream-a"],
    }
    with pytest.raises(ValueError, match="JSON-serializable"):
        CodexMemoryRecord.model_validate({**_record(), "metadata": {"bad": object()}})
    with pytest.raises(ValueError, match="JSON-serializable"):
        CodexMemoryRecord.model_validate({**_record(), "metadata": {"bad": float("nan")}})
    with pytest.raises(ValueError, match="bounded JSON object"):
        CodexMemoryRecord.model_validate({**_record(), "metadata": {"x": "a" * 16_001}})


def test_memory_capture_is_idempotent_and_retrieves_verified_and_inferred(namespace_engines):
    service = CodexMemoryService(namespace_engines, enabled=True)
    verified = service.capture(_record())
    inferred = service.capture(_record(confidence="inferred", statement="The bounded API is the project convention"))
    duplicate = service.capture(_record())

    assert verified["records"][0]["persisted"] is True
    assert inferred["records"][0]["persisted"] is True
    assert duplicate["records"][0]["persisted"] is False
    recalled = service.recall(workspace_id="project-a", query_text="bounded API")
    assert len(recalled["verified"]) == 1
    assert len(recalled["inferred"]) == 1
    assert service.recall(workspace_id="project-a", include_inferred=False)["inferred"] == []
    assert service.review(workspace_id="project-b")["records"] == []


def test_memory_recall_hides_scoped_records_without_authorized_streams(namespace_engines):
    service = CodexMemoryService(namespace_engines, enabled=True)
    service.capture(
        {
            **_record(statement="Mailbox-only fact"),
            "metadata": {
                "acl": {
                    "workspace_id": "project-a",
                    "stream_ids": ["mailbox-a"],
                }
            },
        }
    )

    assert service.recall(workspace_id="project-a")["count"] == 0
    visible = service.recall(
        workspace_id="project-a",
        authorized_stream_ids=("mailbox-a",),
    )
    assert visible["count"] == 1
    visible_records = [*visible["verified"], *visible["inferred"]]
    assert visible_records[0]["metadata"]["acl"]["stream_ids"] == ["mailbox-a"]
    assert service.review(workspace_id="project-a")["count"] == 0
    assert service.review(
        workspace_id="project-a",
        authorized_stream_ids=("mailbox-a",),
    )["count"] == 1
    with pytest.raises(CodexMemoryError, match="different validated payload"):
        service.capture(
            {
                **_record(statement="Mailbox-only fact"),
                "metadata": {
                    "acl": {
                        "workspace_id": "project-a",
                        "stream_ids": ["mailbox-b"],
                    }
                },
            }
        )


def test_memory_capture_creates_one_support_hyperedge_for_multiple_evidence(namespace_engines):
    service = CodexMemoryService(namespace_engines, enabled=True)
    record = _record()
    record["evidence"] = [_evidence("src/one.py"), _evidence("src/two.py")]
    result = service.capture(record)
    memory_id = result["records"][0]["memory_id"]
    assert len(result["records"][0]["evidence"]) == 2

    with _temporary_namespace(
        service.engines.conversation,
        WorkspaceNamespaces("project-a").conv_fg,
    ):
        edges = service.engines.conversation.read.get_edges(limit=None)
    support_edges = [
        edge for edge in edges
        if edge.relation == "supported_by" and edge.source_ids == [memory_id]
    ]
    assert len(support_edges) == 1
    assert len(support_edges[0].target_ids) == 2


def test_disabled_memory_capture_fails_closed(namespace_engines):
    service = CodexMemoryService(namespace_engines, enabled=False)
    with pytest.raises(MemoryDisabledError):
        service.capture(_record())


def test_memory_batches_cannot_cross_workspace_or_use_invalid_filters(namespace_engines):
    service = CodexMemoryService(namespace_engines, enabled=True)
    with pytest.raises(CodexMemoryError, match="one workspace"):
        service.capture([_record(), _record(workspace_id="project-b")])
    with pytest.raises(CodexMemoryError, match="kind is invalid"):
        service.review(workspace_id="project-a", kind="unknown")


def test_memory_capture_rejects_unknown_relationship_targets(namespace_engines):
    service = CodexMemoryService(namespace_engines, enabled=True)
    with pytest.raises(CodexMemoryError, match="missing: memory-not-yet-captured"):
        service.capture({**_record(), "related_memory_ids": ["memory-not-yet-captured"]})


def test_memory_capture_persists_typed_relationship_links(namespace_engines):
    service = CodexMemoryService(namespace_engines, enabled=True)
    prior = service.capture(_record(statement="Prior project convention"))
    prior_id = prior["records"][0]["memory_id"]
    with _temporary_namespace(
        service.engines.conversation,
        WorkspaceNamespaces("project-a").conv_fg,
    ):
        service.engines.conversation.write.add_node(Node(
            id="investigation-1",
            label="Investigation",
            type="entity",
            summary="A bounded investigation",
            doc_id="_conv:investigation-1",
            mentions=[Grounding(spans=[Span.from_dummy_for_conversation("investigation-1")])],
            metadata={"workspace_id": "project-a"},
        ))
    result = service.capture({
        **_record(),
        "related_memory_ids": [prior_id],
        "investigation_ids": ["investigation-1"],
        "supersedes_memory_ids": [prior_id],
        "conflicts_with_memory_ids": [prior_id],
    })
    memory_id = result["records"][0]["memory_id"]
    with _temporary_namespace(
        service.engines.conversation,
        WorkspaceNamespaces("project-a").conv_fg,
    ):
        edges = service.engines.conversation.read.get_edges(limit=None)
    relations = {
        edge.relation: edge.target_ids
        for edge in edges
        if edge.source_ids == [memory_id] and edge.relation != "supported_by"
    }
    assert relations == {
        "related_to": [prior_id],
        "informed_by_investigation": ["investigation-1"],
        "supersedes": [prior_id],
        "conflicts_with": [prior_id],
    }
