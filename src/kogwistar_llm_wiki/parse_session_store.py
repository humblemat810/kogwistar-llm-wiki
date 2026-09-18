"""Durable CAS storage for application-owned layered parse sessions."""

from __future__ import annotations

from typing import Any, Protocol

from .parse_views import ParseFrontierItem, ParseSessionState


class ParseSessionStoreConflict(RuntimeError):
    """Another worker committed this session first."""


class _MetadataStore(Protocol):
    def get_named_projection(self, namespace: str, key: str) -> dict[str, Any] | None: ...

    def compare_and_swap_named_projection(
        self,
        namespace: str,
        key: str,
        payload: dict[str, Any],
        **values: Any,
    ) -> bool: ...


class ParseSessionStore:
    """Persist session/frontier state independently from parser temp files."""

    schema_version = 1

    def __init__(self, metadata: _MetadataStore, *, workspace_id: str) -> None:
        self.metadata = metadata
        self.workspace_id = workspace_id
        self.namespace = f"ws:{workspace_id}:projection_state"

    def key(self, session_id: str) -> str:
        return f"parse_session:{session_id}"

    def get(self, session_id: str) -> tuple[ParseSessionState, list[ParseFrontierItem], int] | None:
        row = self.metadata.get_named_projection(self.namespace, self.key(session_id))
        if row is None:
            return None
        payload = row.get("payload")
        if not isinstance(payload, dict):
            raise TypeError("parse session projection payload must be an object")
        session = ParseSessionState.model_validate(payload.get("session", {}))
        if session.workspace_id != self.workspace_id:
            raise ValueError("parse session workspace does not match store workspace")
        self._validate_session_identity(session)
        frontier = [
            ParseFrontierItem.model_validate(item)
            for item in payload.get("frontier", [])
        ]
        self._validate_frontier(session, frontier)
        return session, frontier, int(row.get("last_authoritative_seq", 0))

    def list_for_source(
        self,
        source_document_id: str,
    ) -> list[tuple[ParseSessionState, list[ParseFrontierItem], int]]:
        """List durable sessions for one source using Kogwistar's projection index."""

        list_projections = getattr(self.metadata, "list_named_projections", None)
        if not callable(list_projections):
            return []
        result: list[tuple[ParseSessionState, list[ParseFrontierItem], int]] = []
        for row in list_projections(self.namespace):
            if not str(row.get("key") or "").startswith("parse_session:"):
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            session = ParseSessionState.model_validate(payload.get("session", {}))
            if session.workspace_id != self.workspace_id or session.source_document_id != source_document_id:
                continue
            frontier = [
                ParseFrontierItem.model_validate(item)
                for item in payload.get("frontier", [])
            ]
            self._validate_frontier(session, frontier)
            result.append((session, frontier, int(row.get("last_authoritative_seq", 0))))
        result.sort(key=lambda item: (item[0].last_progress_at, item[0].session_id), reverse=True)
        return result

    def save(
        self,
        session: ParseSessionState,
        frontier: list[ParseFrontierItem],
        *,
        expected_version: int | None,
    ) -> int:
        if session.workspace_id != self.workspace_id:
            raise ValueError("parse session workspace does not match store workspace")
        self._validate_frontier(session, frontier)
        current = self.get(session.session_id)
        if expected_version is None:
            next_version = 1
            expected_authoritative = None
            expected_materialized = None
        else:
            if current is None or current[2] != expected_version:
                raise ParseSessionStoreConflict("parse session changed before commit")
            self._validate_transition(current[0], session)
            next_version = expected_version + 1
            row = self.metadata.get_named_projection(self.namespace, self.key(session.session_id))
            expected_authoritative = int(row.get("last_authoritative_seq", -1)) if row else -1
            expected_materialized = int(row.get("last_materialized_seq", -1)) if row else -1
        payload = {
            "session": session.model_dump(mode="json"),
            "frontier": [item.model_dump(mode="json") for item in frontier],
        }
        inserted = self.metadata.compare_and_swap_named_projection(
            self.namespace,
            self.key(session.session_id),
            payload,
            expected_last_authoritative_seq=expected_authoritative,
            expected_last_materialized_seq=expected_materialized,
            last_authoritative_seq=next_version,
            last_materialized_seq=next_version,
            projection_schema_version=self.schema_version,
            materialization_status="ready",
        )
        if not inserted:
            raise ParseSessionStoreConflict("parse session CAS lost a race")
        return next_version

    @staticmethod
    def _validate_frontier(
        session: ParseSessionState,
        frontier: list[ParseFrontierItem],
    ) -> None:
        for item in frontier:
            if (
                item.session_id != session.session_id
                or item.workspace_id != session.workspace_id
                or item.source_document_id != session.source_document_id
                or item.source_revision_id != session.source_revision_id
                or item.revision_document_id != session.revision_document_id
                or item.generation_id != session.generation_id
            ):
                raise ValueError("parse frontier item does not belong to its session")

    @staticmethod
    def _validate_session_identity(session: ParseSessionState) -> None:
        if not session.session_id or not session.source_document_id or not session.source_revision_id:
            raise ValueError("parse session is missing a stable source identity")

    @staticmethod
    def _validate_transition(current: ParseSessionState, next_session: ParseSessionState) -> None:
        """Permit progress updates, never a callback changing the parse identity."""

        immutable_fields = (
            "session_id",
            "workspace_id",
            "source_document_id",
            "source_revision_id",
            "source_digest",
            "revision_document_id",
            "generation_id",
            "parser_state",
        )
        changed = [
            field
            for field in immutable_fields
            if getattr(current, field) != getattr(next_session, field)
        ]
        if changed:
            raise ValueError(
                "parse session transition changed immutable identity fields: "
                + ", ".join(changed)
            )


__all__ = ["ParseSessionStore", "ParseSessionStoreConflict"]
