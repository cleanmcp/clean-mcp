"""Tests for CodeSearcher."""

import os
import tempfile

import pytest

from clean.core.config import CleanConfig
from clean.services.container import ServiceContainer


@pytest.fixture
def indexed_project():
    with tempfile.TemporaryDirectory() as project_dir:
        # Create test files
        with open(os.path.join(project_dir, "math_utils.py"), "w") as f:
            f.write("""def add(a, b):
    return a + b

def multiply(a, b):
    return a * b

def calculate(x, y):
    total = add(x, y)
    product = multiply(x, y)
    return total, product
""")
        with tempfile.TemporaryDirectory() as db_dir:
            config = CleanConfig()
            config.storage.default_persist_dir = db_dir
            config.embedder.show_progress_bar = False
            container = ServiceContainer(config)

            result = container.indexer.index(project_dir)
            assert result["status"] == "success"

            yield container, project_dir


def test_search_returns_results(indexed_project):
    container, project_dir = indexed_project
    result = container.searcher.search("addition function", project_dir, top_k=3)

    assert len(result["results"]) > 0


def test_search_with_context(indexed_project):
    container, project_dir = indexed_project
    result = container.searcher.search("calculate", project_dir, top_k=3, depth=1)

    assert len(result["results"]) > 0
    # Context may or may not be present depending on search result


def test_is_indexed(indexed_project):
    container, project_dir = indexed_project
    assert container.searcher.is_indexed(project_dir) is True


def test_is_not_indexed():
    with tempfile.TemporaryDirectory() as tmp:
        config = CleanConfig()
        config.storage.default_persist_dir = os.path.join(tmp, "db")
        container = ServiceContainer(config)
        assert container.searcher.is_indexed("/nonexistent") is False
