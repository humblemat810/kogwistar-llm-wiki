from kogwistar_llm_wiki.app_contracts import MessageChannel, MessageEnvelope
from kogwistar_llm_wiki.app_contracts.messaging import (
    MessageChannel as LegacyMessageChannel,
)
from kogwistar_llm_wiki.app_contracts.messaging import (
    MessageEnvelope as LegacyMessageEnvelope,
)


def test_message_contract_legacy_imports_remain_compatible() -> None:
    assert LegacyMessageEnvelope is MessageEnvelope
    assert LegacyMessageChannel is MessageChannel


def test_message_channel_returns_only_the_public_dto_slice() -> None:
    message = MessageChannel.wrap_message(
        {"job_id": "job-1"},
        target="background",
        intent="request",
        provenance_id="source-1",
    )

    assert message == {
        "target": "background",
        "payload": {"job_id": "job-1"},
        "intent": "request",
        "provenance_id": "source-1",
    }
    assert "internal_trace_id" not in message
