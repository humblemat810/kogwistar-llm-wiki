"""Durable CAS storage for application-owned layered parse sessions."""

from __future__ import annotations

from kogwistar.engine_core import NamedProjectionStore

from .parse_views import ParseFrontierItem, ParseSessionState, SourceRegion


class ParseSessionStoreConflict(RuntimeError):
    """Another worker committed this session first."""


def parse_session_scope_id(region: SourceRegion | None = None) -> str:
    """Group full-source retries together and targeted retries by exact region."""
    if region is None:
        return "full"
    return f"region:{region.start_char}:{region.end_char}"


class ParseSessionStore:
    """Persist session/frontier state independently from parser temp files."""

    __slots__ = ("metadata", "namespace", "workspace_id")
    schema_version = 1

    def __init__(self, metadata: NamedProjectionStore, *, workspace_id: str) -> None:
        self.metadata = metadata
        self.workspace_id = workspace_id
        self.namespace = f"ws:{workspace_id}:projection_state"

    def key(self, session_id: str) -> str:
        return f"parse_session:{session_id}"

    def active_key(self, source_document_id: str, scope_id: str = "full") -> str:
        return f"active_parse_session:{source_document_id}:{scope_id}"

    def active_session_id(
        self,
        source_document_id: str,
        *,
        scope_id: str = "full",
    ) -> str | None:
        row = self.metadata.get_named_projection(
            self.namespace,
            self.active_key(source_document_id, scope_id),
        )
        if row is None:
            return None
        payload = row.get("payload")
        if not isinstance(payload, dict):
            raise TypeError("active parse session projection payload must be an object")
        if (
            str(payload.get("workspace_id") or "") != self.workspace_id
            or str(payload.get("source_document_id") or "") != source_document_id
        ):
            raise ValueError("active parse session projection identity mismatch")
        session_id = str(payload.get("active_session_id") or "").strip()
        return session_id or None

    def activate(self, session: ParseSessionState, *, scope_id: str = "full") -> None:
        """CAS-switch the active derivation for this logical source."""
        if session.workspace_id != self.workspace_id:
            raise ValueError("parse session workspace does not match store workspace")
        if not session.source_document_id or not session.session_id:
            raise ValueError("active parse session requires stable source and session IDs")
        stored = self.get(session.session_id)
        if stored is None:
            raise ValueError("cannot activate a parse session that is not persisted")
        if stored[0].source_document_id != session.source_document_id:
            raise ValueError("active parse session source does not match persisted session")

        if not scope_id or len(scope_id) > 256:
            raise ValueError("parse session scope must be a non-empty string of at most 256 characters")
        key = self.active_key(session.source_document_id, scope_id)
        for _ in range(8):
            row = self.metadata.get_named_projection(self.namespace, key)
            if row is not None:
                if self.active_session_id(session.source_document_id, scope_id=scope_id) == session.session_id:
                    return
                expected_authoritative = int(row.get("last_authoritative_seq", 0))
                expected_materialized = int(row.get("last_materialized_seq", 0))
            else:
                expected_authoritative = None
                expected_materialized = None
            next_version = (expected_authoritative or 0) + 1
            if self.metadata.compare_and_swap_named_projection(
                self.namespace,
                key,
                {
                    "workspace_id": self.workspace_id,
                    "source_document_id": session.source_document_id,
                    "active_session_id": session.session_id,
                },
                expected_last_authoritative_seq=expected_authoritative,
                expected_last_materialized_seq=expected_materialized,
                last_authoritative_seq=next_version,
                last_materialized_seq=next_version,
                projection_schema_version=self.schema_version,
                materialization_status="ready",
            ):
                return
        raise ParseSessionStoreConflict("active parse session CAS repeatedly lost a race")

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
        if session.session_id != session_id:
            raise ValueError("stored parse session identity does not match projection key")
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

        result: list[tuple[ParseSessionState, list[ParseFrontierItem], int]] = []
        for row in self.metadata.list_named_projections(self.namespace):
            if not str(row.get("key") or "").startswith("parse_session:"):
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            session = ParseSessionState.model_validate(payload.get("session", {}))
            if session.workspace_id != self.workspace_id or session.source_document_id != source_document_id:
                continue
            self._validate_session_identity(session)
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
        frontier_ids = [item.frontier_id for item in frontier]
        if len(frontier_ids) != len(set(frontier_ids)):
            raise ValueError("parse frontier contains duplicate item IDs")
        if tuple(frontier_ids) != session.frontier_ids:
            raise ValueError("parse session frontier pointer does not match persisted frontier")
        for item in frontier:
            if (
                item.session_id != session.session_id
                or item.workspace_id != session.workspace_id
                or item.source_document_id != session.source_document_id
                or item.source_revision_id != session.source_revision_id
                or item.revision_document_id != session.revision_document_id
                or item.generation_id != session.generation_id
                or item.region.source_document_id != session.revision_document_id
                or item.depth > session.max_depth
            ):
                raise ValueError("parse frontier item does not belong to its session")

    @staticmethod
    def _validate_session_identity(session: ParseSessionState) -> None:
        if (
            not session.session_id
            or not session.source_document_id
            or not session.source_revision_id
            or not session.source_digest
            or not session.revision_document_id
            or not session.generation_id
        ):
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
            "max_depth",
            "max_frontier_items",
            "max_parser_calls",
            "max_region_chars",
            "token_budget",
            "wall_time_seconds",
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
        if next_session.parser_calls < current.parser_calls:
            raise ValueError("parse session parser call count cannot decrease")
        if not set(current.consumed_frontier_ids).issubset(next_session.consumed_frontier_ids):
            raise ValueError("parse session consumed frontier history cannot be removed")


__all__ = ["ParseSessionStore", "ParseSessionStoreConflict"]
