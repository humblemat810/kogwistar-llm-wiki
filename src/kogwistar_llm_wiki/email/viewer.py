"""ACL-aware, non-mutating email viewer projection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import Any

from .runtime import EmailEvidenceStore


@dataclass(frozen=True, slots=True)
class EmailViewer:
    store: EmailEvidenceStore
    authorize_stream: Callable[[str, str], bool]

    def get(self, *, workspace_id: str, stream_id: str, source_revision_id: str) -> dict[str, object]:
        if not self.authorize_stream(workspace_id, stream_id):
            raise PermissionError("email stream is not authorized for workspace")
        record = self.store.get(
            workspace_id=workspace_id,
            source_revision_id=source_revision_id,
        )
        if record is None or record.stream_id != stream_id:
            return {"status": "not_found"}
        parsed = record.parsed_payload
        mapping = record.mapping_payload
        plain_values = parsed.get("text_plain")
        body = "\n\n".join(str(item) for item in plain_values) if isinstance(plain_values, list) else ""
        subject = str(parsed.get("subject") or "")
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "stream_id": stream_id,
            "source_revision_id": record.source_revision_id,
            "content_sha256": record.content_sha256,
            "subject": subject,
            "date": parsed.get("date_header"),
            "from": _addresses(parsed.get("sender")),
            "to": _addresses(parsed.get("to")),
            "cc": _addresses(parsed.get("cc")),
            "body_text": body,
            "safe_html_preview": f"<h1>{escape(subject)}</h1><pre>{escape(body)}</pre>",
            "attachments": parsed.get("attachments", []),
            "mapping_id": mapping.get("mapping_id") or _mapping_id(mapping),
            "mapping_status": "pending",
            "structural_proposals": mapping,
        }


def _addresses(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [
        {"display_name": str(item.get("display_name") or ""), "address": str(item.get("address") or "")}
        for item in value
        if isinstance(item, dict)
    ]


def _mapping_id(mapping: object) -> str:
    del mapping
    return ""
