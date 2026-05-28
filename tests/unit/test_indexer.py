"""Tests for the CodebaseIndexer."""

import os
import tempfile

import pytest

from clean.core.config import CleanConfig
from clean.services.container import ServiceContainer


@pytest.fixture
def container_with_tmp():
    with tempfile.TemporaryDirectory() as tmp:
        config = CleanConfig()
        config.storage.default_persist_dir = os.path.join(tmp, "db")
        config.embedder.show_progress_bar = False
        container = ServiceContainer(config)
        yield container, tmp


def test_index_fixture_dir(container_with_tmp):
    container, tmp = container_with_tmp
    fixtures = os.path.join(os.path.dirname(__file__), "..", "fixtures")

    result = container.indexer.index(fixtures)
    assert result["status"] == "success"
    assert result["files_processed"] > 0
    assert result["functions_indexed"] > 0


def test_index_nonexistent_path(container_with_tmp):
    container, tmp = container_with_tmp
    result = container.indexer.index("/nonexistent/path/abc123")
    assert result["status"] == "error"


def test_index_empty_dir(container_with_tmp):
    container, tmp = container_with_tmp
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty)

    result = container.indexer.index(empty)
    assert result["status"] == "success"
    assert result["functions_indexed"] == 0
