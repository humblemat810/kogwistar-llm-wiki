from __future__ import annotations

import pytest

from kogwistar_llm_wiki.disambiguation.contact_matching import (
    ContactIdentityObservation,
    ContactPointClaim,
    contact_evidence_snapshot_id,
    discover_contact_match_candidates,
)
from kogwistar_llm_wiki.disambiguation.disambiguation_contracts import (
    DisambiguationDecisionKind,
)

pytestmark = pytest.mark.ci


def _observation(
    entity_id: str,
    stream_id: str,
    *,
    name: str = "",
    point: ContactPointClaim | None = None,
    source_id: str | None = None,
) -> ContactIdentityObservation:
    return ContactIdentityObservation(
        workspace_id="workspace-a",
        stream_id=stream_id,
        entity_id=entity_id,
        source_document_ids=(source_id or f"doc:{entity_id}",),
        evidence_revision_ids=(f"revision:{source_id or entity_id}",),
        observed_at_ms=100,
        display_names=(name,) if name else (),
        contact_points=(point,) if point else (),
    )


def test_shared_claim_links_distinct_channels_as_pending_candidate() -> None:
    phone = ContactPointClaim(channel="phone", value="+14155550123")
    address_side = ContactIdentityObservation(
        workspace_id="workspace-a",
        stream_id="source-a",
        entity_id="contact-address",
        source_document_ids=("doc:contact-address",),
        evidence_revision_ids=("revision:source-message-1",),
        observed_at_ms=100,
        display_names=("Morgan Lee",),
        contact_points=(
            ContactPointClaim(channel="contact", value="address:morgan"),
            phone,
        ),
    )
    chat_side = ContactIdentityObservation(
        workspace_id="workspace-a",
        stream_id="chat-stream",
        entity_id="contact-chat",
        source_document_ids=("chat-event:7",),
        evidence_revision_ids=("revision:chat-event-7",),
        observed_at_ms=100,
        display_names=("M. Lee",),
        contact_points=(
            phone,
            ContactPointClaim(
                channel="im", provider="matrix", value="handle:morgan"
            ),
        ),
    )
    authorized: list[tuple[str, str]] = []

    candidates = discover_contact_match_candidates(
        (chat_side, address_side),
        authorize_stream=lambda workspace, stream: authorized.append((workspace, stream)) is None and True,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.semantic_decision == DisambiguationDecisionKind.AMBIGUOUS
    assert candidate.metadata["match_basis"] == "shared_contact_point"
    assert candidate.metadata["channels"] == "contact,im,phone"
    assert candidate.metadata["matched_channels"] == "phone"
    assert candidate.metadata["automatic_merge"] is False
    assert candidate.source_document_ids == ("chat-event:7", "doc:contact-address")
    assert authorized == [("workspace-a", "chat-stream"), ("workspace-a", "source-a")]
    assert "+14155550123" not in candidate.model_dump_json()
    assert "Morgan Lee" not in candidate.model_dump_json()


def test_exact_name_candidate_is_deterministic_but_single_common_name_is_ignored() -> None:
    observations = (
        _observation("person-1", "authorized-source", name="Jordan Smith"),
        _observation("person-2", "chat", name="jordan  smith"),
        _observation("person-3", "calendar", name="Jordan"),
    )
    def authorize(_workspace, _stream):
        return True

    candidates = discover_contact_match_candidates(observations, authorize_stream=authorize)
    reversed_candidates = discover_contact_match_candidates(
        reversed(observations), authorize_stream=authorize
    )

    assert len(candidates) == 1
    assert candidates == reversed_candidates
    assert candidates[0].metadata["match_basis"] == "exact_name"
    assert candidates[0].metadata["matched_channels"] == ""
    assert candidates[0].score_bundle.review_priority >= 0.5


@pytest.mark.parametrize(
    "name",
    ["\u738b\u529b", "\u5c71\u7530\u592a\u90ce", "\uae40\ubbfc\uc218", "\u3042\u3044"],
)
def test_unspaced_east_asian_full_names_are_reviewable_candidates(name: str) -> None:
    candidates = discover_contact_match_candidates(
        (
            _observation("person-a", "contacts", name=name),
            _observation("person-b", "source", name=name),
        ),
        authorize_stream=lambda *_: True,
    )

    assert len(candidates) == 1
    assert candidates[0].metadata["match_basis"] == "exact_name"
    assert candidates[0].semantic_decision == DisambiguationDecisionKind.AMBIGUOUS
    assert candidates[0].metadata["automatic_merge"] is False


def test_single_character_east_asian_name_stays_below_candidate_threshold() -> None:
    candidates = discover_contact_match_candidates(
        (
            _observation("person-a", "contacts", name="?"),
            _observation("person-b", "source", name="?"),
        ),
        authorize_stream=lambda *_: True,
    )

    assert candidates == ()


def test_similar_name_only_match_remains_review_candidate() -> None:
    candidates = discover_contact_match_candidates(
        (
            _observation("person-1", "source", name="Alicee Chan"),
            _observation("person-2", "chat", name="Alice Chan"),
        ),
        authorize_stream=lambda _workspace, _stream: True,
    )

    assert len(candidates) == 1
    assert candidates[0].metadata["match_basis"] == "similar_name"
    assert candidates[0].semantic_decision == DisambiguationDecisionKind.AMBIGUOUS


def test_contact_point_verification_increases_candidate_score_without_auto_merge() -> None:
    def candidate(verification: str):
        candidates = discover_contact_match_candidates(
            (
                _observation(
                    "person-1",
                    "source",
                    point=ContactPointClaim(
                        channel="contact",
                        value="address:alice",
                        verification=verification,  # type: ignore[arg-type]
                    ),
                ),
                _observation(
                    "person-2",
                    "contacts",
                    point=ContactPointClaim(
                        channel="contact",
                        value="address:alice",
                        verification=verification,  # type: ignore[arg-type]
                    ),
                ),
            ),
            authorize_stream=lambda _workspace, _stream: True,
        )
        return candidates[0]

    claimed = candidate("claimed")
    verified = candidate("provider_verified")
    assert verified.score_bundle.merge_likelihood > claimed.score_bundle.merge_likelihood
    assert verified.metadata["contact_point_verification"] == "provider_verified"
    assert verified.semantic_decision == DisambiguationDecisionKind.AMBIGUOUS


def test_duplicate_contact_claim_order_cannot_change_match_score() -> None:
    first = _observation(
        "person-1",
        "source",
        point=ContactPointClaim(
            channel="contact",
            value="address:alice",
            verification="user_confirmed",
        ),
    )
    second = ContactIdentityObservation(
        workspace_id="workspace-a",
        stream_id="contacts",
        entity_id="person-2",
        source_document_ids=("doc:person-2",),
        evidence_revision_ids=("revision:person-2",),
        observed_at_ms=100,
        contact_points=(
            ContactPointClaim(channel="contact", value="address:alice"),
            ContactPointClaim(
                channel="contact",
                value="address:alice",
                verification="provider_verified",
            ),
        ),
    )

    def discover(right: ContactIdentityObservation):
        return discover_contact_match_candidates(
            (first, right),
            authorize_stream=lambda _workspace, _stream: True,
        )[0]

    forward = discover(second)
    reverse = discover(
        second.model_copy(update={"contact_points": tuple(reversed(second.contact_points))})
    )

    assert forward == reverse
    assert forward.metadata["contact_point_verification"] == "provider_verified"
    assert forward.semantic_decision == DisambiguationDecisionKind.AMBIGUOUS


def test_changed_source_revision_changes_evidence_snapshot_not_candidate_identity() -> None:
    first = _observation(
        "person-1",
        "source",
        point=ContactPointClaim(channel="contact", value="address:alice"),
    )
    second = _observation(
        "person-2",
        "contacts",
        point=ContactPointClaim(channel="contact", value="address:alice"),
    )
    revised = second.model_copy(update={"evidence_revision_ids": ("revision:contacts-v2",)})
    def authorize(_workspace, _stream):
        return True

    original_candidate = discover_contact_match_candidates(
        (first, second), authorize_stream=authorize
    )[0]
    revised_candidate = discover_contact_match_candidates(
        (first, revised), authorize_stream=authorize
    )[0]

    assert revised_candidate.candidate_key == original_candidate.candidate_key
    assert revised_candidate.evidence_snapshot_id != original_candidate.evidence_snapshot_id


def test_changed_identity_claim_changes_evidence_snapshot_even_for_same_source_revision() -> None:
    left = _observation(
        "person-1",
        "source",
        point=ContactPointClaim(channel="contact", value="address:alice"),
    )
    right = _observation(
        "person-2",
        "contacts",
        point=ContactPointClaim(channel="contact", value="address:alice"),
    )
    changed_right = right.model_copy(
        update={
            "display_names": ("Alice Example",),
            "contact_points": (
                ContactPointClaim(channel="contact", value="address:alice-new"),
            ),
        }
    )

    before = contact_evidence_snapshot_id(left, right, basis="shared_contact_point")
    after = contact_evidence_snapshot_id(left, changed_right, basis="shared_contact_point")

    assert before != after
    assert "address:alice" not in before


def test_contact_evidence_snapshot_is_workspace_scoped() -> None:
    left = _observation(
        "person-1",
        "source",
        point=ContactPointClaim(channel="contact", value="address:alice"),
    )
    right = _observation(
        "person-2",
        "contacts",
        point=ContactPointClaim(channel="contact", value="address:alice"),
    )
    other_workspace = tuple(
        observation.model_copy(update={"workspace_id": "workspace-b"})
        for observation in (left, right)
    )

    assert contact_evidence_snapshot_id(
        left, right, basis="shared_contact_point"
    ) != contact_evidence_snapshot_id(
        *other_workspace, basis="shared_contact_point"
    )


def test_authorization_fails_closed_before_comparison() -> None:
    checked: list[str] = []

    def authorize(_workspace: str, stream: str) -> bool:
        checked.append(stream)
        return stream != "private-chat"

    with pytest.raises(PermissionError, match="not authorized"):
        discover_contact_match_candidates(
            (
                _observation("person-1", "authorized-source", name="Jordan Smith"),
                _observation("person-2", "private-chat", name="Jordan Smith"),
            ),
            authorize_stream=authorize,
        )
    assert checked == ["authorized-source", "private-chat"]


def test_candidate_scan_rejects_mixed_workspaces_before_acl_calls() -> None:
    first = _observation("person-1", "authorized-source", name="Jordan Smith")
    second = _observation("person-2", "chat", name="Jordan Smith").model_copy(
        update={"workspace_id": "workspace-b"}
    )
    called = False

    def authorize(_workspace: str, _stream: str) -> bool:
        nonlocal called
        called = True
        return True

    with pytest.raises(ValueError, match="workspace boundaries"):
        discover_contact_match_candidates((first, second), authorize_stream=authorize)
    assert not called


@pytest.mark.parametrize(
    "changes",
    [
        {"display_names": ("A" * 257,)},
        {"display_names": ("Jordan Smith\nforged",)},
        {"source_document_ids": ("doc:" + "x" * 513,)},
        {"contact_points": tuple(
            ContactPointClaim(channel="contact", value=f"address:{index}")
            for index in range(129)
        )},
    ],
)
def test_contact_observation_rejects_unbounded_or_controlled_identity_claims(changes) -> None:
    values = {
        "workspace_id": "workspace-a",
        "stream_id": "source",
        "entity_id": "person-1",
        "source_document_ids": ("doc:person-1",),
        "evidence_revision_ids": ("revision:1",),
        "observed_at_ms": 100,
        "display_names": ("Jordan Smith",),
    }
    values.update(changes)

    with pytest.raises(ValueError):
        ContactIdentityObservation(**values)


def test_matcher_rejects_boolean_or_noninteger_work_bounds() -> None:
    observation = _observation("person-1", "authorized-source", name="Jordan Smith")
    for kwargs in ({"max_observations": True}, {"max_candidates": 1.5}):
        with pytest.raises(ValueError, match="bounds must be positive"):
            discover_contact_match_candidates(
                (observation,),
                authorize_stream=lambda *_: True,
                **kwargs,
            )

    with pytest.raises(ValueError, match="fuzzy_name_threshold"):
        discover_contact_match_candidates(
            (observation,),
            authorize_stream=lambda *_: True,
            fuzzy_name_threshold=True,
        )


def test_candidate_scan_stops_at_bound_plus_one_before_acl_or_matching() -> None:
    consumed: list[int] = []
    authorized: list[str] = []

    def observations():
        for index in range(20):
            consumed.append(index)
            yield _observation(f"person-{index}", "authorized-source", name="Jordan Smith")

    with pytest.raises(ValueError, match="exceeds max_observations"):
        discover_contact_match_candidates(
            observations(),
            authorize_stream=lambda _workspace, stream: authorized.append(stream) is None,
            max_observations=2,
        )

    assert consumed == [0, 1, 2]
    assert authorized == []


@pytest.mark.parametrize(
    ("channel", "value", "provider"),
    [
        ("", "opaque-claim", None),
        ("channel name", "opaque-claim", None),
        ("x" * 65, "opaque-claim", None),
    ],
)
def test_channel_identifiers_require_adapter_normalization(
    channel: str, value: str, provider: str | None
) -> None:
    with pytest.raises(ValueError):
        ContactPointClaim(channel=channel, value=value, provider=provider)  # type: ignore[arg-type]
