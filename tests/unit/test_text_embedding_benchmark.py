from __future__ import annotations

from scripts.benchmark_text_embedding_models import (
    MODEL_IDS,
    _make_text,
    _make_text_for_token_target,
)


def test_text_benchmark_matrix_contains_requested_models() -> None:
    assert set(MODEL_IDS) == {
        "all-minilm-l6-v2",
        "multilingual-e5-small",
        "paraphrase-multilingual-minilm-l12-v2",
        "gte-multilingual-base",
        "nomic-embed-text-v2-moe",
        "bge-m3",
        "qwen3-embedding-0.6b",
    }


def test_text_fixture_has_exact_requested_word_count() -> None:
    assert len(_make_text(128).split()) == 128
    assert len(_make_text(2048).split()) == 2048


def test_token_fixture_stays_within_requested_target() -> None:
    class FakeTokenizer:
        def __call__(self, text: str, **_: object) -> dict[str, list[str]]:
            return {"input_ids": ["token"] * (len(text.split()) + 1)}

    class FakeModel:
        tokenizer = FakeTokenizer()

    text = _make_text_for_token_target(FakeModel(), 64)
    assert len(text.split()) + 1 <= 64
