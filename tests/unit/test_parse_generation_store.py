from __future__ import annotations

import pytest
from kogwistar.engine_core.in_memory_meta import InMemoryMetaStore

from kogwistar_llm_wiki.parse_generation_store import (
    ParseGenerationStore,
    ParseGenerationStoreConflict,
)
from kogwistar_llm_wiki.parse_views import (
    ParseGeneration,
    ParseGenerationCommit,
    ParseGenerationMember,
    SourceRegion,
)


def _evidence() -> tuple[ParseGeneration, ParseGenerationCommit, list[ParseGenerationMember]]:
    generation = ParseGeneration(
        generation_id="generation-1",
        workspace_id="demo",
        source_document_id="logical-source",
        source_revision_id="revision-1",
        source_digest="digest",
        revision_document_id="revision-doc-1",
        parser_profile="coarse-v1",
        parser_version="1",
    )
    member = ParseGenerationMember(
        member_id="member-1",
        generation_id=generation.generation_id,
        workspace_id="demo",
        source_document_id=generation.source_document_id,
        source_revision_id=generation.source_revision_id,
        revision_document_id=generation.revision_document_id,
        region=SourceRegion(source_document_id="revision-doc-1", start_char=0, end_char=10),
        semantic_id="semantic-1",
    )
    commit = ParseGenerationCommit(
        commit_id="commit-1",
        generation_id=generation.generation_id,
        workspace_id="demo",
        source_document_id=generation.source_document_id,
        source_revision_id=generation.source_revision_id,
        member_ids=(member.member_id,),
    )
    return generation, commit, [member]


def test_generation_commit_is_idempotent_and_immutable() -> None:
    generation, commit, members = _evidence()
    store = ParseGenerationStore(InMemoryMetaStore(), workspace_id="demo")
    assert store.commit(generation, commit, members) == 1
    assert store.commit(generation, commit, members) == 1

    changed = commit.model_copy(update={"attempt": 2})
    with pytest.raises(ParseGenerationStoreConflict, match="reused"):
        store.commit(generation, changed, members)


def test_generation_commit_rejects_cross_workspace_or_partial_batches() -> None:
    generation, commit, members = _evidence()
    store = ParseGenerationStore(InMemoryMetaStore(), workspace_id="other")
    with pytest.raises(ValueError, match="workspace"):
        store.commit(generation, commit, members)
    store = ParseGenerationStore(InMemoryMetaStore(), workspace_id="demo")
    with pytest.raises(ValueError, match="member IDs"):
        store.commit(generation, commit.model_copy(update={"member_ids": ()}), members)
    with pytest.raises(ValueError, match="source identity"):
        store.commit(
            generation,
            commit.model_copy(update={"source_revision_id": "other-revision"}),
            members,
        )


def test_generation_retry_rejects_changed_member_payload() -> None:
    generation, commit, members = _evidence()
    store = ParseGenerationStore(InMemoryMetaStore(), workspace_id="demo")
    store.commit(generation, commit, members)
    changed_member = members[0].model_copy(update={"semantic_id": "different"})
    with pytest.raises(ParseGenerationStoreConflict, match="member ID"):
        store.commit(generation, commit, [changed_member])
