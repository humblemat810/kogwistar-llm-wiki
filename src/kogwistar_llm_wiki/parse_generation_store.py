"""Idempotent persistence for immutable layered-parse generation evidence."""

from __future__ import annotations

from typing import Any, Protocol

from .parse_views import ParseGeneration, ParseGenerationCommit, ParseGenerationMember


class ParseGenerationStoreConflict(RuntimeError):
    """Another writer changed a generation before this commit."""


class _MetadataStore(Protocol):
    def get_named_projection(self, namespace: str, key: str) -> dict[str, Any] | None: ...

    def compare_and_swap_named_projection(
        self,
        namespace: str,
        key: str,
        payload: dict[str, Any],
        **values: Any,
    ) -> bool: ...


class ParseGenerationStore:
    """Store generation evidence separately from mutable parser session state."""

    schema_version = 1

    def __init__(self, metadata: _MetadataStore, *, workspace_id: str) -> None:
        self.metadata = metadata
        self.workspace_id = workspace_id
        self.namespace = f"ws:{workspace_id}:projection_state"

    def key(self, generation_id: str) -> str:
        return f"parse_generation:{generation_id}"

    def commit(
        self,
        generation: ParseGeneration,
        commit: ParseGenerationCommit,
        members: list[ParseGenerationMember],
    ) -> int:
        """Commit one immutable batch, returning its projection version.

        Retrying the same commit ID is idempotent.  A different commit must
        use the current CAS version and can never replace existing evidence.
        """

        self._validate(generation, commit, members)
        key = self.key(generation.generation_id)
        row = self.metadata.get_named_projection(self.namespace, key)
        if row is None:
            payload = {
                "generation": generation.model_dump(mode="json"),
                "commits": {commit.commit_id: commit.model_dump(mode="json")},
                "members": {member.member_id: member.model_dump(mode="json") for member in members},
            }
            expected_authoritative = None
            expected_materialized = None
            next_version = 1
        else:
            existing = row.get("payload")
            if not isinstance(existing, dict):
                raise TypeError("parse generation projection payload must be an object")
            commits = dict(existing.get("commits") or {})
            stored_generation = existing.get("generation")
            if stored_generation != generation.model_dump(mode="json"):
                raise ParseGenerationStoreConflict("generation ID was reused with different evidence")
            existing_commit = commits.get(commit.commit_id)
            if existing_commit is not None:
                if existing_commit != commit.model_dump(mode="json"):
                    raise ParseGenerationStoreConflict("commit ID was reused with different evidence")
                existing_members = dict(existing.get("members") or {})
                for member in members:
                    previous = existing_members.get(member.member_id)
                    if previous != member.model_dump(mode="json"):
                        raise ParseGenerationStoreConflict("member ID was reused with different evidence")
                return int(row.get("last_authoritative_seq", 0))
            existing_members = dict(existing.get("members") or {})
            for member in members:
                previous = existing_members.get(member.member_id)
                if previous is not None and previous != member.model_dump(mode="json"):
                    raise ParseGenerationStoreConflict("member ID was reused with different evidence")
            commits[commit.commit_id] = commit.model_dump(mode="json")
            existing_members.update({member.member_id: member.model_dump(mode="json") for member in members})
            payload = {
                "generation": existing.get("generation", generation.model_dump(mode="json")),
                "commits": commits,
                "members": existing_members,
            }
            expected_authoritative = int(row.get("last_authoritative_seq", -1))
            expected_materialized = int(row.get("last_materialized_seq", -1))
            next_version = expected_authoritative + 1
        inserted = self.metadata.compare_and_swap_named_projection(
            self.namespace,
            key,
            payload,
            expected_last_authoritative_seq=expected_authoritative,
            expected_last_materialized_seq=expected_materialized,
            last_authoritative_seq=next_version,
            last_materialized_seq=next_version,
            projection_schema_version=self.schema_version,
            materialization_status="ready",
        )
        if not inserted:
            raise ParseGenerationStoreConflict("generation commit lost a CAS race")
        return next_version

    def _validate(
        self,
        generation: ParseGeneration,
        commit: ParseGenerationCommit,
        members: list[ParseGenerationMember],
    ) -> None:
        if generation.workspace_id != self.workspace_id or commit.workspace_id != self.workspace_id:
            raise ValueError("parse generation workspace does not match store workspace")
        if commit.generation_id != generation.generation_id:
            raise ValueError("generation commit does not match generation")
        if (
            commit.source_document_id != generation.source_document_id
            or commit.source_revision_id != generation.source_revision_id
        ):
            raise ValueError("generation commit source identity does not match generation")
        member_ids = {member.member_id for member in members}
        if set(commit.member_ids) != member_ids:
            raise ValueError("generation commit member IDs must match the member batch")
        for member in members:
            if (
                member.workspace_id != self.workspace_id
                or member.generation_id != generation.generation_id
                or member.source_document_id != generation.source_document_id
                or member.source_revision_id != generation.source_revision_id
                or member.revision_document_id != generation.revision_document_id
            ):
                raise ValueError("generation member is outside the committed generation")


__all__ = ["ParseGenerationStore", "ParseGenerationStoreConflict"]
