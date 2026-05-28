"""Integration test: incremental re-indexing."""

import os
import tempfile

import pytest

from clean.core.config import CleanConfig
from clean.services.container import ServiceContainer


@pytest.fixture
def project_with_container():
    with tempfile.TemporaryDirectory() as project_dir:
        with open(os.path.join(project_dir, "a.py"), "w") as f:
            f.write("def func_a():\n    return 'a'\n")
        with open(os.path.join(project_dir, "b.py"), "w") as f:
            f.write("def func_b():\n    return func_a()\n")

        with tempfile.TemporaryDirectory() as db_dir:
            config = CleanConfig()
            config.storage.default_persist_dir = db_dir
            config.embedder.show_progress_bar = False
            container = ServiceContainer(config)
            yield container, project_dir


def test_incremental_only_processes_changes(project_with_container):
    container, project_dir = project_with_container

    # First full index
    r1 = container.indexer.index(project_dir)
    assert r1["status"] == "success"
    assert r1["files_processed"] == 2
    initial_count = r1["functions_indexed"]

    # No changes — should skip
    r2 = container.indexer.index(project_dir)
    assert r2["files_processed"] == 0
    assert r2["functions_indexed"] == initial_count

    # Modify one file
    with open(os.path.join(project_dir, "a.py"), "w") as f:
        f.write("def func_a():\n    return 'modified'\n\ndef func_c():\n    pass\n")

    # Should only process the modified file
    r3 = container.indexer.index(project_dir)
    assert r3["status"] == "success"
    assert r3["files_processed"] >= 1
    assert r3.get("incremental") is True


def test_add_new_file(project_with_container):
    container, project_dir = project_with_container

    # First index
    container.indexer.index(project_dir)

    # Add new file
    with open(os.path.join(project_dir, "c.py"), "w") as f:
        f.write("def func_c():\n    return 'new'\n")

    r = container.indexer.index(project_dir)
    assert r["status"] == "success"
    assert r["files_processed"] >= 1


def test_delete_file_removes_entities(project_with_container):
    container, project_dir = project_with_container

    r1 = container.indexer.index(project_dir)
    count1 = r1["functions_indexed"]

    os.remove(os.path.join(project_dir, "b.py"))

    r2 = container.indexer.index(project_dir)
    assert r2["functions_indexed"] < count1
