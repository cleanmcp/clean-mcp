"""Tests for local sentence-transformers embedder."""

import pytest

from clean.embedding.local import SentenceTransformerEmbedder


@pytest.fixture(scope="module")
def embedder():
    """Share a single embedder instance across tests (model load is expensive)."""
    return SentenceTransformerEmbedder()


def test_dimension(embedder):
    assert embedder.dimension == 384


def test_embed_query(embedder):
    result = embedder.embed_query("test query")
    assert isinstance(result, list)
    assert len(result) == 384


def test_embed_batch(embedder):
    results = embedder.embed_batch(["code1", "code2", "code3"])
    assert len(results) == 3
    for r in results:
        assert len(r) == 384


def test_embed_batch_empty(embedder):
    results = embedder.embed_batch([])
    assert results == []
