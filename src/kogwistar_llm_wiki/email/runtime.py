"""Scoped, proposal-only integration with ``kogwistar-email-plugin``.

The plugin remains optional.  This module owns product orchestration and
durable evidence storage, while parsing and structural mapping stay in the
plugin repository.  Email headers are retained as claims and are never used
as workspace or ACL authority.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..ingest_pipeline import IngestPipeline
from ..models import IngestPipelineRequest
from ..utils import _temporary_namespace
from .ontology import EmailOntologyBinding


class EmailPluginUnavailable(RuntimeError):
    """Raised when email integration is requested without the optional plugin."""


@dataclass(frozen=True, slots=True)
class EmailIngestRequest:
    workspace_id: str
    stream_id: str
    source_key: str
    raw_bytes: bytes
    source_revision_id: str
    title: str = "Email message"

    def __post_init__(self) -> None:
        for name in ("workspace_id", "stream_id", "source_key", "source_revision_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if not isinstance(self.raw_bytes, bytes) or not self.raw_bytes:
            raise ValueError("raw_bytes must be non-empty bytes")


@dataclass(frozen=True, slots=True)
class EmailEvidenceRecord:
    workspace_id: str
    stream_id: str
    source_key: str
    source_revision_id: str
    content_sha256: str
    parsed_payload: Mapping[str, object]
    mapping_payload: Mapping[str, object]
    blob_ref: str
    source_document_id: str | None = None


@dataclass(frozen=True, slots=True)
class EmailIngestResult:
    workspace_id: str
    stream_id: str
    source_key: str
    source_revision_id: str
    content_sha256: str
    source_document_id: str
    derivation_id: str
    mapping_id: str
    status: str = "proposals_pending"


class EmailEvidenceStore(Protocol):
    def put(self, request: EmailIngestRequest, record: EmailEvidenceRecord) -> None:
        """Persist immutable bytes and derivation payloads idempotently."""

    def get(self, *, workspace_id: str, source_revision_id: str) -> EmailEvidenceRecord | None:
        """Read only records from the requested workspace."""


class InMemoryEmailEvidenceStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], EmailEvidenceRecord] = {}
        self._raw: dict[tuple[str, str], bytes] = {}

    def put(self, request: EmailIngestRequest, record: EmailEvidenceRecord) -> None:
        _validate_evidence_write(request, record)
        key = (request.workspace_id, request.source_revision_id)
        existing = self._records.get(key)
        if existing is not None and existing != record:
            raise ValueError("email source revision already exists with different evidence")
        self._records[key] = record
        self._raw.setdefault(key, request.raw_bytes)

    def get(self, *, workspace_id: str, source_revision_id: str) -> EmailEvidenceRecord | None:
        return self._records.get((workspace_id, source_revision_id))

    def raw_bytes(self, *, workspace_id: str, source_revision_id: str) -> bytes | None:
        return self._raw.get((workspace_id, source_revision_id))


class SQLiteEmailEvidenceStore:
    """Small app-owned immutable evidence store, separate from graph state."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_evidence (
                    workspace_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_revision_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    raw_bytes BLOB NOT NULL,
                    parsed_json TEXT NOT NULL,
                    mapping_json TEXT NOT NULL,
                    blob_ref TEXT NOT NULL,
                    source_document_id TEXT,
                    PRIMARY KEY (workspace_id, source_revision_id)
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(email_evidence)").fetchall()
            }
            if "source_document_id" not in columns:
                connection.execute(
                    "ALTER TABLE email_evidence ADD COLUMN source_document_id TEXT"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def put(self, request: EmailIngestRequest, record: EmailEvidenceRecord) -> None:
        _validate_evidence_write(request, record)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT content_sha256, parsed_json, mapping_json, source_document_id "
                "FROM email_evidence "
                "WHERE workspace_id = ? AND source_revision_id = ?",
                (request.workspace_id, request.source_revision_id),
            ).fetchone()
            if existing is not None:
                expected = (
                    record.content_sha256,
                    json.dumps(record.parsed_payload, sort_keys=True, separators=(",", ":")),
                    json.dumps(record.mapping_payload, sort_keys=True, separators=(",", ":")),
                    record.source_document_id,
                )
                if tuple(existing) != expected:
                    raise ValueError("email source revision already exists with different evidence")
                return
            connection.execute(
                "INSERT INTO email_evidence ("
                "workspace_id, stream_id, source_key, source_revision_id, "
                "content_sha256, raw_bytes, parsed_json, mapping_json, "
                "blob_ref, source_document_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request.workspace_id,
                    request.stream_id,
                    request.source_key,
                    request.source_revision_id,
                    record.content_sha256,
                    request.raw_bytes,
                    json.dumps(record.parsed_payload, sort_keys=True, separators=(",", ":")),
                    json.dumps(record.mapping_payload, sort_keys=True, separators=(",", ":")),
                    record.blob_ref,
                    record.source_document_id,
                ),
            )

    def get(self, *, workspace_id: str, source_revision_id: str) -> EmailEvidenceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM email_evidence WHERE workspace_id = ? AND source_revision_id = ?",
                (workspace_id, source_revision_id),
            ).fetchone()
        if row is None:
            return None
        return EmailEvidenceRecord(
            workspace_id=str(row["workspace_id"]),
            stream_id=str(row["stream_id"]),
            source_key=str(row["source_key"]),
            source_revision_id=str(row["source_revision_id"]),
            content_sha256=str(row["content_sha256"]),
            parsed_payload=json.loads(str(row["parsed_json"])),
            mapping_payload=json.loads(str(row["mapping_json"])),
            blob_ref=str(row["blob_ref"]),
            source_document_id=(
                str(row["source_document_id"])
                if row["source_document_id"] is not None
                else None
            ),
        )

    def raw_bytes(self, *, workspace_id: str, source_revision_id: str) -> bytes | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT raw_bytes FROM email_evidence WHERE workspace_id = ? AND source_revision_id = ?",
                (workspace_id, source_revision_id),
            ).fetchone()
        return None if row is None else bytes(row["raw_bytes"])


class _EmailPlugin(Protocol):
    def parse_rfc822(self, raw_bytes: bytes, *, source_revision_id: str) -> Any: ...

    def derive_structural_mapping(self, parsed: Any, *, stream_id: str) -> Any: ...


def load_email_plugin() -> _EmailPlugin:
    try:
        module = importlib.import_module("kogwistar_email_plugin")
    except ImportError as exc:
        raise EmailPluginUnavailable(
            "email integration requires the optional kogwistar-email-plugin package"
        ) from exc
    parser = getattr(module, "parse_rfc822", None)
    mapper = getattr(module, "derive_structural_mapping", None)
    if not callable(parser) or not callable(mapper):
        raise EmailPluginUnavailable("installed email plugin lacks parser/mapping contracts")
    return module


class EmailRuntime:
    """Parse, persist, and expose email derivations without accepting mappings."""

    def __init__(
        self,
        *,
        pipeline: IngestPipeline,
        store: EmailEvidenceStore | None = None,
        plugin: _EmailPlugin | None = None,
        ontology: EmailOntologyBinding | None = None,
        authorize_stream: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.store = store or InMemoryEmailEvidenceStore()
        self.plugin = plugin
        self.ontology = ontology
        self.authorize_stream = authorize_stream or (lambda _workspace_id, _stream_id: True)

    def ingest(self, request: EmailIngestRequest) -> EmailIngestResult:
        if not self.authorize_stream(request.workspace_id, request.stream_id):
            raise PermissionError("email stream is not authorized for workspace")
        plugin = self.plugin or load_email_plugin()
        ontology = self.ontology or EmailOntologyBinding.from_plugin(plugin)
        parsed = plugin.parse_rfc822(
            request.raw_bytes,
            source_revision_id=request.source_revision_id,
        )
        mapping = plugin.derive_structural_mapping(parsed, stream_id=request.stream_id)
        parsed_payload = _payload(parsed)
        mapping_payload = _payload(mapping)
        content_sha256 = str(parsed_payload.get("content_sha256") or "")
        if content_sha256 != hashlib.sha256(request.raw_bytes).hexdigest():
            raise ValueError("email parser content digest does not match immutable bytes")
        mapping_id = str(getattr(mapping, "mapping_id", ""))
        if not mapping_id:
            raise ValueError("email mapping did not provide a deterministic mapping_id")
        mapping_payload["mapping_id"] = mapping_id
        ontology.validate_mapping(mapping_payload)
        mapping_payload["ontology"] = ontology.identity
        source_document_id = _source_document_id(
            request.workspace_id,
            request.stream_id,
            request.source_key,
        )
        record = EmailEvidenceRecord(
            workspace_id=request.workspace_id,
            stream_id=request.stream_id,
            source_key=request.source_key,
            source_revision_id=request.source_revision_id,
            content_sha256=content_sha256,
            parsed_payload=parsed_payload,
            mapping_payload=mapping_payload,
            blob_ref=f"sha256:{content_sha256}",
            source_document_id=source_document_id,
        )
        self.store.put(request, record)

        text = _display_text(parsed_payload)
        pipeline_request = IngestPipelineRequest(
            workspace_id=request.workspace_id,
            source_uri=f"email://{request.stream_id}/{request.source_key}",
            title=request.title,
            raw_text=text,
            source_format="message/rfc822",
            parser_mode="email_plugin",
            parser_lane="email_structural",
            promotion_mode="pending",
            provenance_policy="required",
            provenance={
                "workspace_id": request.workspace_id,
                "source_document_id": source_document_id,
                "source_revision_id": request.source_revision_id,
                "source_digest": content_sha256,
                "email_blob_ref": f"sha256:{content_sha256}",
            },
        )
        self.pipeline.register_source(
            request=pipeline_request,
            source_document_id=source_document_id,
            namespace=self.pipeline.namespaces_for(request.workspace_id).conv_fg,
        )
        derivation_id = _derivation_id(request, mapping_id)
        node = self.pipeline._artifact_node(
            request=pipeline_request,
            source_document_id=source_document_id,
            namespace=self.pipeline.namespaces_for(request.workspace_id).source_space,
            node_id=derivation_id,
            artifact_kind="email_structural_mapping",
            lane="background",
            visibility="internal",
            label=f"Email Mapping: {request.title}",
            summary="Immutable structural email mapping proposal; acceptance is separate.",
            extra_metadata={
                "workspace_id": request.workspace_id,
                "stream_id": request.stream_id,
                "source_key": request.source_key,
                "email_source_revision_id": request.source_revision_id,
                "email_content_sha256": content_sha256,
                "email_blob_ref": f"sha256:{content_sha256}",
                "mapping_id": mapping_id,
                "ontology": ontology.identity,
                "mapping": mapping_payload,
                "parsed_email": parsed_payload,
                "acceptance_status": "pending",
                "graph_space": "source",
            },
        )
        with _temporary_namespace(
            self.pipeline.engines.kg,
            self.pipeline.namespaces_for(request.workspace_id).source_space,
        ):
            if not self.pipeline.engines.kg.read.node_exists(ids=[derivation_id]):
                self.pipeline.engines.kg.write.add_node(node)
        return EmailIngestResult(
            workspace_id=request.workspace_id,
            stream_id=request.stream_id,
            source_key=request.source_key,
            source_revision_id=request.source_revision_id,
            content_sha256=content_sha256,
            source_document_id=source_document_id,
            derivation_id=derivation_id,
            mapping_id=mapping_id,
        )


def _validate_evidence_write(
    request: EmailIngestRequest,
    record: EmailEvidenceRecord,
) -> None:
    """Keep immutable evidence identity and bytes bound at the storage edge."""
    if (
        record.workspace_id != request.workspace_id
        or record.stream_id != request.stream_id
        or record.source_key != request.source_key
        or record.source_revision_id != request.source_revision_id
    ):
        raise ValueError("email evidence identity does not match ingest request")
    content_sha256 = hashlib.sha256(request.raw_bytes).hexdigest()
    if record.content_sha256 != content_sha256:
        raise ValueError("email evidence content digest does not match immutable bytes")
    if record.blob_ref != f"sha256:{content_sha256}":
        raise ValueError("email evidence blob_ref does not match content digest")


def _payload(value: Any) -> dict[str, object]:
    method = getattr(value, "to_payload", None)
    if not callable(method):
        raise TypeError("email plugin result must expose to_payload()")
    payload = method()
    if not isinstance(payload, dict):
        raise TypeError("email plugin payload must be an object")
    return json.loads(json.dumps(payload, sort_keys=True))


def _display_text(payload: Mapping[str, object]) -> str:
    subject = str(payload.get("subject") or "").strip()
    plain = payload.get("text_plain")
    paragraphs = [str(item) for item in plain] if isinstance(plain, list) else []
    text = "\n\n".join(item for item in paragraphs if item.strip())
    result = f"Subject: {subject}\n\n{text}".strip() if subject else text.strip()
    return result or "Email message has no displayable text"


def _source_document_id(workspace_id: str, stream_id: str, source_key: str) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}\0{stream_id}\0{source_key}".encode()
    ).hexdigest()
    return f"email-source:{digest}"


def _derivation_id(request: EmailIngestRequest, mapping_id: str) -> str:
    digest = hashlib.sha256(
        f"{request.workspace_id}\0{request.stream_id}\0{request.source_revision_id}\0{mapping_id}".encode()
    ).hexdigest()
    return f"email-derivation:{digest}"
