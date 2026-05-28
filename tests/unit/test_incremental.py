"""Tests for incremental indexing."""

import os
import tempfile

import pytest

from clean.core.config import CleanConfig
from clean.services.container import ServiceContainer


@pytest.fixture
def project_dir():
    with tempfile.TemporaryDirectory() as tmp:
        # Create initial files
        with open(os.path.join(tmp, "hello.py"), "w") as f:
            f.write("def hello():\n    return 'hello'\n")
        with open(os.path.join(tmp, "world.py"), "w") as f:
            f.write("def world():\n    return 'world'\n")
        yield tmp


@pytest.fixture
def container(project_dir):
    with tempfile.TemporaryDirectory() as db_dir:
        config = CleanConfig()
        config.storage.default_persist_dir = db_dir
        config.embedder.show_progress_bar = False
        yield ServiceContainer(config)


def test_incremental_detects_no_changes(project_dir, container):
    # First index
    r1 = container.indexer.index(project_dir)
    assert r1["status"] == "success"
    count1 = r1["functions_indexed"]

    # Second index — no changes
    r2 = container.indexer.index(project_dir)
    assert r2["status"] == "success"
    assert r2["files_processed"] == 0  # Nothing to reprocess
    assert r2["functions_indexed"] == count1


def test_incremental_detects_modified_file(project_dir, container):
    # First index
    r1 = container.indexer.index(project_dir)
    assert r1["status"] == "success"

    # Modify a file
    with open(os.path.join(project_dir, "hello.py"), "w") as f:
        f.write(
            "def hello():\n    return 'modified hello'\n\ndef new_func():\n    pass\n"
        )

    # Re-index
    r2 = container.indexer.index(project_dir)
    assert r2["status"] == "success"
    assert r2["files_processed"] >= 1  # At least the modified file
    assert r2.get("incremental") is True


def test_incremental_detects_deleted_file(project_dir, container):
    # First index
    r1 = container.indexer.index(project_dir)
    count1 = r1["functions_indexed"]

    # Delete a file
    os.remove(os.path.join(project_dir, "world.py"))

    # Re-index
    r2 = container.indexer.index(project_dir)
    assert r2["status"] == "success"
    assert r2["functions_indexed"] < count1


def test_force_full_reindex(project_dir, container):
    # First index
    container.indexer.index(project_dir)

    # Force full
    r2 = container.indexer.index(project_dir, force_full=True)
    assert r2["status"] == "success"
    assert r2.get("incremental") is False
    assert r2["files_processed"] > 0
