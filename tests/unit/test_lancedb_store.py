"""Tests for LanceDB store."""

import tempfile

import pytest

from clean.core.config import StorageConfig
from clean.core.models import CodeEntity, ProjectState, FileState
from clean.core.types import EntityKind, Language
from clean.storage.lancedb import LanceDBStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        config = StorageConfig(default_persist_dir=tmp)
        s = LanceDBStore(config)
        yield s


@pytest.fixture
def entities():
    return [
        CodeEntity(
            name="greet",
            file_path="/test/sample.py",
            code="def greet(): pass",
            line_start=1,
            line_end=1,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
            calls=("format_greeting",),
            embedding=[0.1] * 384,
        ),
        CodeEntity(
            name="format_greeting",
            file_path="/test/sample.py",
            code='def format_greeting(): return "hi"',
            line_start=5,
            line_end=5,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
            calls=(),
            embedding=[0.2] * 384,
        ),
        CodeEntity(
            name="process",
            file_path="/test/main.py",
            code='def process(): greet("world")',
            line_start=1,
            line_end=1,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
            calls=("greet",),
            embedding=[0.3] * 384,
        ),
    ]


def test_upsert_and_count(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    count = store.upsert(pid, entities)
    assert count == 3
    assert store.count(pid) == 3


def test_search(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)

    results = store.search(pid, [0.1] * 384, top_k=2)
    assert len(results) == 2
    assert results[0].similarity > 0


def test_get_by_file(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)

    found = store.get_by_file(pid, "/test/sample.py")
    names = [e.name for e in found]
    assert "greet" in names
    assert "format_greeting" in names


def test_get_by_name(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)

    found = store.get_by_name(pid, "greet")
    assert len(found) >= 1
    assert found[0].name == "greet"


def test_delete_by_file(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)

    deleted = store.delete_by_file(pid, "/test/sample.py")
    assert deleted == 2
    assert store.count(pid) == 1


def test_clear(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)
    assert store.count(pid) == 3

    store.clear(pid)
    assert store.count(pid) == 0


def test_project_state_roundtrip(store):
    pid = "test_project"
    state = ProjectState(
        project_id=pid,
        root_path="/test",
        files={
            "/test/a.py": FileState(
                file_path="/test/a.py",
                content_hash="abc123",
                entity_count=3,
                last_indexed_at=1234.0,
            ),
        },
        total_entities=3,
        git_head="abc123def",
    )

    store.save_project_state(pid, state)
    loaded = store.get_project_state(pid)

    assert loaded is not None
    assert loaded.project_id == pid
    assert loaded.root_path == "/test"
    assert loaded.total_entities == 3
    assert loaded.git_head == "abc123def"
    assert "/test/a.py" in loaded.files
    assert loaded.files["/test/a.py"].content_hash == "abc123"


def test_get_by_names_batch(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)

    found = store.get_by_names(pid, ["greet", "process"])
    names = {e.name for e in found}
    assert "greet" in names
    assert "process" in names
    assert "format_greeting" not in names


def test_get_by_names_empty(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)

    assert store.get_by_names(pid, []) == []


def test_get_by_names_no_matches(store, entities):
    pid = "test_project"
    store.initialize(pid, 384)
    store.upsert(pid, entities)

    assert store.get_by_names(pid, ["nonexistent", "also_missing"]) == []


def test_project_state_none_when_missing(store):
    assert store.get_project_state("nonexistent") is None
