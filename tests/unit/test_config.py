"""Tests for configuration."""

from clean.core.config import CleanConfig


def test_default_config():
    c = CleanConfig()
    assert c.embedder.model_name == "all-MiniLM-L6-v2"
    assert c.embedder.embedding_dimensions == 384
    assert c.search.default_top_k == 5
    assert ".py" in c.parser.extension_languages


def test_from_env(monkeypatch):
    monkeypatch.setenv("CLEAN_EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("CLEAN_DEFAULT_TOP_K", "10")
    monkeypatch.setenv("CLEAN_DEBUG", "true")

    c = CleanConfig.from_env()
    assert c.embedder.model_name == "test-model"
    assert c.search.default_top_k == 10
    assert c.debug is True


def test_from_env_invalid_int(monkeypatch):
    monkeypatch.setenv("CLEAN_DEFAULT_TOP_K", "not_a_number")
    c = CleanConfig.from_env()
    assert c.search.default_top_k == 5  # default preserved


def test_indexer_skip_dirs():
    c = CleanConfig()
    assert "node_modules" in c.indexer.skip_dirs
    assert ".git" in c.indexer.skip_dirs


def test_storage_persist_path():
    c = CleanConfig()
    assert c.storage.default_persist_path == ".clean"
