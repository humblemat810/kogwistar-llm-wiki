"""Agent-boundary source validation, fetching, and safe serialization."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urlparse


def validate_supplied_provenance(
    provenance: Mapping[str, Any],
    *,
    workspace_id: str,
    source_uri: str,
    raw_text: str,
    require_identity: bool = False,
) -> None:
    declared_workspace = provenance.get("workspace_id")
    if require_identity and not declared_workspace:
        raise ValueError("required provenance must include workspace_id")
    if declared_workspace is not None and str(declared_workspace) != workspace_id:
        raise ValueError("provenance workspace_id does not match request workspace")
    declared_uri = provenance.get("source_uri")
    if require_identity and not declared_uri:
        raise ValueError("required provenance must include source_uri")
    if declared_uri is not None and str(declared_uri) != source_uri:
        raise ValueError("provenance source_uri does not match request source_uri")
    start = provenance.get("start_char")
    end = provenance.get("end_char")
    excerpt = provenance.get("excerpt")
    if start is None and end is None and excerpt is None:
        return
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(raw_text):
        raise ValueError("provenance span must be a valid half-open [start_char, end_char) range")
    if excerpt is not None and str(excerpt) != raw_text[start:end]:
        raise ValueError("provenance excerpt does not match raw_text span")


def validate_agent_source_uri(source_uri: str) -> None:
    """Reject local paths and unsafe URI forms at the agent boundary."""
    if not source_uri or "\\" in source_uri:
        raise ValueError("source_uri must be a non-local URI")
    if len(source_uri) >= 2 and source_uri[1] == ":" and source_uri[0].isalpha():
        raise ValueError("local filesystem source_uri values are not accepted")
    parsed = urlparse(source_uri)
    if not parsed.scheme or parsed.scheme.lower() in {"file", "data", "javascript"}:
        raise ValueError("local filesystem and executable source_uri schemes are not accepted")
    if (
        parsed.scheme.lower() in {"http", "https"}
        and (not parsed.hostname or parsed.username or parsed.password or parsed.fragment)
    ):
        raise ValueError("source_uri must be a credential-free http(s) URL")


def validate_reingest_revision(existing: Mapping[str, Any], provenance: object) -> None:
    if not isinstance(provenance, Mapping):
        return
    requested = str(provenance.get("source_revision_id") or "").strip()
    if not requested:
        return
    known: set[str] = set()
    for revision in existing.get("revisions") or []:
        if not isinstance(revision, Mapping):
            continue
        known.add(str(revision.get("id") or ""))
        metadata = revision.get("metadata")
        if isinstance(metadata, Mapping):
            known.add(str(metadata.get("source_revision_id") or ""))
    if requested not in known:
        raise ValueError("provenance source_revision_id does not resolve to the existing source")


def fetch_source_text(source_uri: str) -> str:
    parsed = urlparse(source_uri)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("source_uri fetching requires a credential-free http(s) URL")
    allowed = {
        item.strip().lower()
        for item in os.getenv("LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    if parsed.hostname.lower() not in allowed:
        raise ValueError("source_uri host is not allowlisted by LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS")
    max_bytes = int(os.getenv("LLM_WIKI_SOURCE_FETCH_MAX_BYTES", "5000000"))
    timeout = max(1.0, float(os.getenv("LLM_WIKI_SOURCE_FETCH_TIMEOUT_SECONDS", "30")))

    class NoRedirect(urllib_request.HTTPRedirectHandler):
        def redirect_request(self, *_args: object, **_kwargs: object):
            raise ValueError("source_uri redirects are not permitted")

    request = urllib_request.Request(
        source_uri,
        headers={"accept": "text/plain, text/markdown, text/html"},
    )
    with urllib_request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError("source_uri response exceeds LLM_WIKI_SOURCE_FETCH_MAX_BYTES")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("source_uri response exceeds LLM_WIKI_SOURCE_FETCH_MAX_BYTES")
    return data.decode("utf-8")


def redact_source_text(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): redact_source_text(item)
            for key, item in value.items()
            if key != "source_raw_text"
        }
    if isinstance(value, list):
        return [redact_source_text(item) for item in value]
    return value


def decode_metadata_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return {str(key): item for key, item in decoded.items()}
    return None


def node_json(
    node: object | None,
    *,
    redact_source_text: bool = False,
) -> dict[str, object] | None:
    if node is None:
        return None
    dump = getattr(node, "model_dump", None)
    if callable(dump):
        try:
            value = dump(dump_format="json")
            return redact_source_text_value(value) if redact_source_text else value
        except TypeError:
            value = dump(mode="json")
            return redact_source_text_value(value) if redact_source_text else value
    value = {"id": str(getattr(node, "id", "")), "metadata": dict(getattr(node, "metadata", {}) or {})}
    return redact_source_text_value(value) if redact_source_text else value


def redact_source_text_value(value: object) -> object:
    """Compatibility alias used internally by ``node_json``."""
    return redact_source_text(value)
