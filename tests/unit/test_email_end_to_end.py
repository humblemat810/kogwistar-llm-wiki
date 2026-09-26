from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.email import (
    EmailConnectorBinding,
    EmailRuntime,
    EmailSyncService,
    InMemoryEmailConnectorBindingStore,
    InMemoryEmailEvidenceStore,
    InMemoryEmailMailboxEventStore,
    InMemoryEmailReviewStateStore,
    InMemoryEmailSyncLeaseStore,
)
from kogwistar_llm_wiki.workbench.workbench_api import WorkbenchApi
from tests._helpers.fake_email_plugin import FakeRfc822SourceAdapter

RAW_EMAIL = (
    b"From: Alice <alice@example.test>\r\n"
    b"To: Bob <bob@example.test>\r\n"
    b"Subject: End to end project update\r\n"
    b"Message-ID: <e2e@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"The project remains on schedule.\r\n"
)


@dataclass(frozen=True)
class OneBatchAdapter:
    source: FakeRfc822SourceAdapter
    raw_bytes: bytes

    def sync(self, snapshot: object | None = None) -> object:
        message = self.source.read(self.raw_bytes)
        return type(
            "Batch",
            (),
            {
                "messages": (message,) if snapshot is None else (),
                "events": (
                    SimpleNamespace(
                        kind="message_discovered",
                        stream_id=message.stream_id,
                        source_key=message.source_key,
                        source_revision_id=message.source_revision_id,
                        content_sha256=message.content_sha256,
                    ),
                )
                if snapshot is None
                else (),
                "snapshot": SimpleNamespace(
                    stream_id=message.stream_id,
                    source_kind="rfc822",
                    known_keys=(message.source_key,),
                    content_sha256_by_key={message.source_key: message.content_sha256},
                ),
                "has_more": False,
            },
        )()


@pytest.mark.ci
def test_email_plugin_to_viewer_and_memory_end_to_end(pipeline, monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_CODEX_MEMORY_ENABLED", "true")

    def authorize(workspace: str, stream: str) -> bool:
        return workspace == "email-workspace" and bool(stream)

    evidence = InMemoryEmailEvidenceStore()
    reviews = InMemoryEmailReviewStateStore()
    runtime = EmailRuntime(
        pipeline=pipeline,
        store=evidence,
        authorize_stream=authorize,
    )
    binding_store = InMemoryEmailConnectorBindingStore()
    binding = binding_store.put(
        EmailConnectorBinding(
            tenant_id="tenant-a",
            workspace_id="email-workspace",
            connector_id="local-rfc822",
            account_principal_id="bob",
            mailbox_id="bob@example.test",
            credential_ref="local/no-secret",
        )
    )
    plugin_adapter = OneBatchAdapter(
        source=FakeRfc822SourceAdapter(binding, source_key="uid:1"),
        raw_bytes=RAW_EMAIL,
    )
    sync = EmailSyncService(
        runtime=runtime,
        binding_store=binding_store,
        lease_store=InMemoryEmailSyncLeaseStore(),
        event_store=InMemoryEmailMailboxEventStore(),
        authorize_stream=authorize,
        clock_ms=lambda: 100,
    )

    synced = sync.sync_binding(
        workspace_id=binding.workspace_id,
        connector_id=binding.connector_id,
        owner_id="worker-a",
        adapter_factory=lambda _binding, _credential_ref: plugin_adapter,
    )
    assert len(synced.ingested_source_revision_ids) == 1
    revision_id = synced.ingested_source_revision_ids[0]

    api = WorkbenchApi(
        pipeline,
        email_evidence_store=evidence,
        email_review_store=reviews,
        email_authorize_stream=authorize,
    )
    viewed = api.view_email(
        workspace_id=binding.workspace_id,
        stream_id=binding.stream_id,
        source_revision_id=revision_id,
    )
    assert viewed["status"] == "ok"
    assert viewed["mapping_status"] == "pending"
    record = evidence.get(
        workspace_id=binding.workspace_id,
        source_revision_id=revision_id,
    )
    assert record is not None
    assert viewed["source_document_id"] == record.source_document_id

    proposal = api.propose_email_mapping(
        workspace_id=binding.workspace_id,
        stream_id=binding.stream_id,
        source_revision_id=revision_id,
        source_document_id=str(viewed["source_document_id"]),
    )
    assert proposal["status"] == "proposed"
    assert api.accept_email_mapping(
        workspace_id=binding.workspace_id,
        stream_id=binding.stream_id,
        source_revision_id=revision_id,
        source_document_id=str(viewed["source_document_id"]),
        confirmed=False,
    )["status"] == "confirmation_required"
    accepted = api.accept_email_mapping(
        workspace_id=binding.workspace_id,
        stream_id=binding.stream_id,
        source_revision_id=revision_id,
        source_document_id=str(viewed["source_document_id"]),
        confirmed=True,
    )
    assert accepted["status"] == "applied"

    memory_proposal = api.propose_email_memory(
        workspace_id=binding.workspace_id,
        stream_id=binding.stream_id,
        source_revision_id=revision_id,
        statement="The project remains on schedule.",
    )
    assert memory_proposal["status"] == "proposed"
    captured = api.promote_email_memory(
        workspace_id=binding.workspace_id,
        stream_id=binding.stream_id,
        source_revision_id=revision_id,
        statement="The project remains on schedule.",
        confirmed=True,
    )
    assert captured["status"] == "captured"
