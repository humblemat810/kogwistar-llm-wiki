from __future__ import annotations

from scripts.benchmark_text_embedding_models import MODEL_IDS, _make_text


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
