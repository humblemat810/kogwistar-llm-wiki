"""Small deterministic email-plugin contract used by host-side tests.

The real parser, mailbox adapters, ontology bundle, and viewer are tested in
the standalone ``kogwistar-email-plugin`` repository.  These fakes exercise
only the LLM-Wiki host boundary without importing that optional package.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html import escape
from types import ModuleType, SimpleNamespace

from kogwistar.ontology import (
    OntologyClassDescriptor,
    OntologyPackage,
    OntologyRelationDescriptor,
)

from kogwistar_llm_wiki.email.bindings import EmailConnectorBinding


def email_ontology_json() -> dict[str, object]:
    package = OntologyPackage.create(
        ontology_id="email",
        version="1.0.0",
        title="Test email ontology",
        descriptors=[
            OntologyClassDescriptor(
                descriptor_id="EmailMessage",
                name="Email message",
                aliases=("mail message", "email"),
            ),
            OntologyClassDescriptor(
                descriptor_id="EmailAddress",
                name="Email address",
                aliases=("sender", "recipient"),
            ),
            OntologyClassDescriptor(
                descriptor_id="Attachment",
                name="Attachment",
                aliases=("file attachment",),
            ),
            OntologyClassDescriptor(
                descriptor_id="CalendarInvitation",
                name="Calendar invitation",
                aliases=("meeting invitation",),
            ),
            OntologyClassDescriptor(
                descriptor_id="EmailThread",
                name="Email thread",
                aliases=("conversation",),
            ),
            OntologyRelationDescriptor(
                descriptor_id="message_exchange",
                name="Message exchange",
                source_class_ids=("EmailMessage",),
                target_class_ids=("EmailAddress",),
            ),
            OntologyRelationDescriptor(
                descriptor_id="attachment_of",
                name="Attachment of",
                source_class_ids=("Attachment",),
                target_class_ids=("EmailMessage",),
            ),
        ],
    )
    return package.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class FakeParsedEmail:
    source_revision_id: str
    content_sha256: str
    subject: str | None
    message_id: str | None
    text_plain: tuple[str, ...]
    parser_profile: str = "fake-rfc822-v1"

    def to_payload(self) -> dict[str, object]:
        return {
            "source_revision_id": self.source_revision_id,
            "content_sha256": self.content_sha256,
            "subject": self.subject,
            "message_id": self.message_id,
            "text_plain": list(self.text_plain),
            "parser_profile": self.parser_profile,
        }


@dataclass(frozen=True, slots=True)
class FakeEmailMapping:
    payload: dict[str, object]
    mapping_id: str

    def to_payload(self) -> dict[str, object]:
        return dict(self.payload)


class FakeEmailPlugin:
    """Minimal parser/mapping/ontology surface required by the host runtime."""

    @staticmethod
    def email_ontology_json() -> dict[str, object]:
        return email_ontology_json()

    @staticmethod
    def parse_rfc822(raw_bytes: bytes, *, source_revision_id: str) -> FakeParsedEmail:
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        body = message.get_body(preferencelist=("plain",))
        text = body.get_content() if body is not None else ""
        return FakeParsedEmail(
            source_revision_id=source_revision_id,
            content_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            subject=message.get("Subject"),
            message_id=message.get("Message-ID"),
            text_plain=(str(text),) if str(text).strip() else (),
        )

    @staticmethod
    def derive_structural_mapping(
        parsed: FakeParsedEmail,
        *,
        stream_id: str,
    ) -> FakeEmailMapping:
        message_entity_id = f"fake-email:{parsed.source_revision_id}:message"
        entities: list[dict[str, object]] = [
            {
                "entity_id": message_entity_id,
                "class_id": "EmailMessage",
                "properties": {
                    "subject": [parsed.subject] if parsed.subject else [],
                },
            }
        ]
        payload: dict[str, object] = {
            "stream_id": stream_id,
            "source_revision_id": parsed.source_revision_id,
            "content_sha256": parsed.content_sha256,
            "parser_profile": parsed.parser_profile,
            "entities": entities,
            "relations": [],
            "header_claims": [],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return FakeEmailMapping(
            payload=payload,
            mapping_id=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        )


class FakeEmailViewer:
    def __init__(self, store, *, authorize_stream, review_store=None) -> None:
        self.store = store
        self.authorize_stream = authorize_stream
        self.review_store = review_store

    def get(self, *, workspace_id: str, stream_id: str, source_revision_id: str) -> dict[str, object]:
        if not self.authorize_stream(workspace_id, stream_id):
            raise PermissionError("email stream is not authorized")
        record = self.store.get(
            workspace_id=workspace_id,
            source_revision_id=source_revision_id,
        )
        if record is None or record.stream_id != stream_id:
            return {"status": "not_found"}
        parsed = record.parsed_payload
        plain_text = parsed.get("text_plain", [])
        body_text = "\n\n".join(str(item) for item in plain_text)
        mapping_id = str(record.mapping_payload.get("mapping_id") or "")
        review = (
            self.review_store.get(
                workspace_id=workspace_id,
                source_revision_id=source_revision_id,
                mapping_id=mapping_id,
            )
            if self.review_store is not None and mapping_id
            else None
        )
        review_status = str(getattr(review, "status", "pending"))
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "stream_id": stream_id,
            "source_revision_id": record.source_revision_id,
            "source_document_id": record.source_document_id,
            "content_sha256": record.content_sha256,
            "subject": str(parsed.get("subject") or ""),
            "body_text": body_text,
            "safe_html_preview": (
                f"<h1>{escape(str(parsed.get('subject') or ''))}</h1>"
                f"<pre>{escape(body_text)}</pre>"
            ),
            "mapping_id": mapping_id,
            "mapping_status": review_status,
            "review": {
                "status": review_status,
                "source_document_id": getattr(review, "source_document_id", None),
                "patch_id": getattr(review, "patch_id", None),
            },
            "structural_proposals": dict(record.mapping_payload),
        }


@dataclass(frozen=True, slots=True)
class FakeRfc822SourceAdapter:
    """Bounded source adapter fake for host sync orchestration tests."""

    binding: EmailConnectorBinding
    source_key: str = "message"

    def read(self, raw_bytes: bytes) -> SimpleNamespace:
        digest = hashlib.sha256(raw_bytes).hexdigest()
        return SimpleNamespace(
            stream_id=self.binding.stream_id,
            source_key=self.source_key,
            raw_bytes=raw_bytes,
            source_revision_id=f"rfc822:{self.binding.stream_id}:{self.source_key}:{digest}",
            content_sha256=digest,
        )


def plugin_object() -> FakeEmailPlugin:
    return FakeEmailPlugin()


def plugin_module() -> ModuleType:
    module = ModuleType("kogwistar_email_plugin")
    module.EmailViewer = FakeEmailViewer
    module.email_ontology_json = email_ontology_json
    module.parse_rfc822 = FakeEmailPlugin.parse_rfc822
    module.derive_structural_mapping = FakeEmailPlugin.derive_structural_mapping
    module.render_email_viewer_plugin = lambda: (
        '<main><h1>Email plugin</h1><p id="status"></p>'
        '<script>document.getElementById("status").textContent = "ready";</script>'
        "</main>"
    )
    return module


__all__ = [
    "FakeEmailPlugin",
    "FakeEmailViewer",
    "FakeParsedEmail",
    "FakeRfc822SourceAdapter",
    "email_ontology_json",
    "plugin_module",
    "plugin_object",
]
