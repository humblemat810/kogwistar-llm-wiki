from __future__ import annotations

import pytest

from kogwistar_llm_wiki import (
    AddressBookEntry,
    ContactIdentityObservation,
    ContactPointClaim,
    build_address_book_projection,
    compose_contact_observation_providers,
)
from kogwistar_llm_wiki.disambiguation.contact_matching import (
    discover_contact_match_candidates,
)
from kogwistar_llm_wiki.disambiguation.disambiguation_contracts import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationResolutionSource,
)

pytestmark = pytest.mark.ci


def _observation(
    entity_id: str,
    stream_id: str,
    value: str,
    *,
    channel: str = "email",
    provider: str | None = None,
) -> ContactIdentityObservation:
    return ContactIdentityObservation(
        workspace_id="workspace-a",
        stream_id=stream_id,
        entity_id=entity_id,
        source_document_ids=(f"doc:{entity_id}",),
        evidence_revision_ids=(f"revision:{entity_id}",),
        observed_at_ms=100,
        display_names=("Morgan Lee",),
        contact_points=(ContactPointClaim(channel=channel, provider=provider, value=value),),  # type: ignore[arg-type]
    )


def _decision(
    left: ContactIdentityObservation,
    right: ContactIdentityObservation,
    *,
    same: bool,
    canonical: str | None = None,
) -> DisambiguationCandidate:
    candidate = discover_contact_match_candidates(
        (left, right),
        authorize_stream=lambda _workspace, _stream: True,
    )[0]
    return candidate.model_copy(
        update={
            "artifact_status": DisambiguationArtifactStatus.RESOLVED,
            "resolution_source": DisambiguationResolutionSource.USER,
            "semantic_decision": (
                DisambiguationDecisionKind.SAME_ENTITY
                if same
                else DisambiguationDecisionKind.DISTINCT_ENTITIES
            ),
            "canonical_entity_id": canonical if same else None,
        }
    )


def test_pending_similarity_does_not_merge_and_claim_provenance_is_preserved() -> None:
    observations = (
        _observation("mail-person", "mail-a", "morgan@example.test"),
        _observation("chat-person", "chat-a", "@morgan:example.test", channel="im", provider="matrix"),
    )
    pending = _decision(*observations, same=False).model_copy(
        update={
            "artifact_status": DisambiguationArtifactStatus.PENDING,
            "semantic_decision": DisambiguationDecisionKind.AMBIGUOUS,
        }
    )

    entries = build_address_book_projection(
        observations,
        (pending,),
        authorize_stream=lambda _workspace, _stream: True,
    )

    assert len(entries) == 2
    assert all(isinstance(entry, AddressBookEntry) for entry in entries)
    assert {claim.stream_id for entry in entries for claim in entry.contact_points} == {
        "mail-a",
        "chat-a",
    }


def test_reviewed_same_groups_channels_under_canonical_id_but_keeps_claim_origins() -> None:
    observations = (
        _observation("mail-person", "mail-a", "morgan@example.test"),
        _observation("chat-person", "chat-a", "@morgan:example.test", channel="im", provider="matrix"),
    )

    entries = build_address_book_projection(
        observations,
        (_decision(*observations, same=True, canonical="mail-person"),),
        authorize_stream=lambda _workspace, _stream: True,
    )

    assert len(entries) == 1
    assert entries[0].contact_id == "mail-person"
    assert entries[0].entity_ids == ("chat-person", "mail-person")
    assert {claim.stream_id for claim in entries[0].contact_points} == {"mail-a", "chat-a"}
    assert {claim.evidence_revision_ids[0] for claim in entries[0].contact_points} == {
        "revision:mail-person",
        "revision:chat-person",
    }
    later_distinct = _decision(*observations, same=False)
    assert len(
        build_address_book_projection(
            observations,
            (later_distinct,),
            authorize_stream=lambda _workspace, _stream: True,
        )
    ) == 2


def test_old_same_decision_does_not_merge_contacts_after_evidence_revision_changes() -> None:
    original = (
        _observation("mail-person", "mail-a", "morgan@example.test"),
        _observation("chat-person", "chat-a", "@morgan:example.test", channel="im", provider="matrix"),
    )
    accepted = _decision(*original, same=True, canonical="mail-person")
    revised = tuple(
        item.model_copy(update={"evidence_revision_ids": (item.evidence_revision_ids[0] + ":new",)})
        for item in original
    )

    entries = build_address_book_projection(
        revised,
        (accepted,),
        authorize_stream=lambda _workspace, _stream: True,
    )

    assert len(entries) == 2


def test_old_same_decision_does_not_merge_when_claim_changes_without_revision_change() -> None:
    original = (
        _observation("mail-person", "mail-a", "alice@example.test"),
        _observation("chat-person", "chat-a", "alice@example.test", channel="email"),
    )
    accepted = _decision(*original, same=True, canonical="mail-person")
    changed_claims = (
        original[0],
        original[1].model_copy(
            update={
                "display_names": ("Different Alice",),
                "contact_points": (
                    ContactPointClaim(channel="email", value="other@example.test"),
                ),
            }
        ),
    )

    entries = build_address_book_projection(
        changed_claims,
        (accepted,),
        authorize_stream=lambda _workspace, _stream: True,
    )

    assert len(entries) == 2


def test_transitive_same_decisions_conflicting_with_distinct_fail_closed() -> None:
    observations = tuple(
        _observation(entity_id, f"stream-{entity_id}", f"{entity_id}@example.test")
        for entity_id in ("a", "b", "c")
    )
    by_id = {observation.entity_id: observation for observation in observations}
    decisions = (
        _decision(by_id["a"], by_id["b"], same=True, canonical="a"),
        _decision(by_id["b"], by_id["c"], same=True, canonical="b"),
        _decision(by_id["a"], by_id["c"], same=False),
    )

    with pytest.raises(ValueError, match="SAME/DISTINCT conflict"):
        build_address_book_projection(
            observations,
            decisions,
            authorize_stream=lambda _workspace, _stream: True,
        )


def test_authorization_covers_all_observation_and_decision_streams_before_projection() -> None:
    observations = (
        _observation("mail-person", "mail-a", "morgan@example.test"),
        _observation("chat-person", "chat-a", "@morgan:example.test", channel="im", provider="matrix"),
    )
    checked: list[str] = []

    with pytest.raises(PermissionError, match="not authorized"):
        build_address_book_projection(
            observations,
            (_decision(*observations, same=True, canonical="mail-person"),),
            authorize_stream=lambda _workspace, stream: checked.append(stream) is None and stream != "chat-a",
        )

    assert checked == ["chat-a"]


def test_channel_providers_compose_deterministically_with_acl_and_identity_scope() -> None:
    email = _observation("email-person", "mail-a", "morgan@example.test")
    chat = _observation(
        "chat-person",
        "chat-a",
        "@morgan:example.test",
        channel="im",
        provider="matrix",
    )
    calls: list[str] = []

    def adapter(name: str, observation: ContactIdentityObservation):
        def provide(workspace: str, limit: int, authorize):
            assert workspace == "workspace-a" and limit == 10
            assert authorize(workspace, observation.stream_id)
            calls.append(name)
            return (observation,)

        return provide

    provider = compose_contact_observation_providers(
        {"z-chat": adapter("chat", chat), "a-email": adapter("email", email)},
        max_observations=10,
    )
    result = provider("workspace-a", 10, lambda *_: True)

    assert calls == ["email", "chat"]
    assert tuple(item.entity_id for item in result) == ("chat-person", "email-person")


def test_channel_provider_composition_rejects_id_collisions_and_partial_overflow() -> None:
    first = _observation("same-person", "mail-a", "morgan@example.test")
    second = _observation("same-person", "chat-a", "@morgan:example.test", channel="im", provider="matrix")
    providers = compose_contact_observation_providers(
        {
            "email": lambda *_: (first,),
            "matrix": lambda *_: (second,),
        },
    )
    with pytest.raises(ValueError, match="entity IDs collide"):
        providers("workspace-a", 10, lambda *_: True)

    oversized = compose_contact_observation_providers(
        {
            "a": lambda *_: (first,),
            "b": lambda *_: (_observation("second-person", "mail-b", "second@example.test"),),
        },
        max_observations=2,
    )
    with pytest.raises(ValueError, match="exceeds limit"):
        oversized("workspace-a", 1, lambda *_: True)


def test_address_book_projection_bounds_iterables_before_acl_or_processing() -> None:
    observation_reads: list[int] = []

    def observations():
        for index in range(20):
            observation_reads.append(index)
            yield _observation(f"person-{index}", f"stream-{index}", f"{index}@example.test")

    authorized: list[str] = []
    with pytest.raises(ValueError, match="exceeds configured bounds"):
        build_address_book_projection(
            observations(),
            (),
            authorize_stream=lambda _workspace, stream: authorized.append(stream) is None,
            max_observations=2,
        )
    assert observation_reads == [0, 1, 2]
    assert authorized == []

    decision_reads: list[int] = []

    def decisions():
        for index in range(20):
            decision_reads.append(index)
            yield object()

    with pytest.raises(ValueError, match="exceeds configured bounds"):
        build_address_book_projection(
            (_observation("person", "mail", "person@example.test"),),
            decisions(),
            authorize_stream=lambda *_: True,
            max_decisions=2,
        )
    assert decision_reads == [0, 1, 2]


def test_same_contact_point_in_separate_workspaces_never_cross_links() -> None:
    left = _observation("person-a", "mail-a", "shared@example.test")
    right = _observation("person-b", "mail-b", "shared@example.test").model_copy(
        update={"workspace_id": "workspace-b"}
    )

    with pytest.raises(ValueError, match="workspace boundaries"):
        discover_contact_match_candidates(
            (left, right),
            authorize_stream=lambda _workspace, _stream: True,
        )

    left_book = build_address_book_projection(
        (left,),
        (),
        authorize_stream=lambda workspace, stream: workspace == "workspace-a" and stream == "mail-a",
    )
    right_book = build_address_book_projection(
        (right,),
        (),
        authorize_stream=lambda workspace, stream: workspace == "workspace-b" and stream == "mail-b",
    )

    assert len(left_book) == len(right_book) == 1
    assert left_book[0].workspace_id == "workspace-a"
    assert right_book[0].workspace_id == "workspace-b"
    assert left_book[0].entity_ids == ("person-a",)
    assert right_book[0].entity_ids == ("person-b",)
