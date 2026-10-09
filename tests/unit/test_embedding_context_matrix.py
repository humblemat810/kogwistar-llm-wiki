from __future__ import annotations

from scripts.benchmark_embedding_context_matrix import (
    Service,
    _ovis_input,
    _qwen_payload,
)


def test_qwen_context_payload_keeps_text_and_hashed_image() -> None:
    payload = _qwen_payload(
        Service("qwen", "http://qwen", "qwen", ""),
        {"fingerprint": "profile"},
        words=4,
        modality="text_image",
    )

    item = payload["items"][0]
    assert item["modality"] == "image"
    assert item["text"] == "context context context context"
    assert item["asset"]["content_type"] == "image/png"
    assert len(item["asset"]["sha256"]) == 64


def test_ovis_context_payload_uses_openai_multimodal_message_shape() -> None:
    messages = _ovis_input(words=3, modality="text_image")

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": messages[0]["content"][0]["image_url"]["url"]}},
                {"type": "text", "text": "context context context"},
            ],
        }
    ]


def test_image_context_shape_does_not_add_text_context() -> None:
    messages = _ovis_input(words=8192, modality="image")

    assert len(messages[0]["content"]) == 1
    assert messages[0]["content"][0]["type"] == "image_url"
