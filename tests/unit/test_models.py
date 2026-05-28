"""Tests for core data models."""

from clean.core.models import (
    CodeEntity,
    FileState,
    ProjectState,
    SearchResult,
    SearchContext,
)
from clean.core.types import EntityKind, Language


def test_code_entity_id_generation():
    e = CodeEntity(
        name="greet",
        file_path="/test/sample.py",
        code="def greet(): pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
    )
    assert len(e.id) == 32
    assert e.id  # not empty


def test_code_entity_id_deterministic():
    e1 = CodeEntity(
        name="greet",
        file_path="/test/sample.py",
        code="def greet(): pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
    )
    e2 = CodeEntity(
        name="greet",
        file_path="/test/sample.py",
        code="def greet(): pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
    )
    assert e1.id == e2.id


def test_code_entity_with_embedding():
    e = CodeEntity(
        name="greet",
        file_path="/test.py",
        code="pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
    )
    assert e.embedding is None
    e2 = e.with_embedding([0.1, 0.2])
    assert e2.embedding == [0.1, 0.2]
    assert e.embedding is None  # original unchanged


def test_code_entity_with_called_by():
    e = CodeEntity(
        name="greet",
        file_path="/test.py",
        code="pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
    )
    e2 = e.with_called_by(("caller1", "caller2"))
    assert e2.called_by == ("caller1", "caller2")
    assert e.called_by == ()


def test_code_entity_frozen():
    e = CodeEntity(
        name="greet",
        file_path="/test.py",
        code="pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
    )
    try:
        e.name = "other"
        assert False, "Should have raised"
    except AttributeError:
        pass


def test_file_state():
    fs = FileState(
        file_path="/test.py",
        content_hash="abc123",
        entity_count=5,
        last_indexed_at=1234.0,
    )
    assert fs.file_path == "/test.py"
    assert fs.content_hash == "abc123"


def test_project_state():
    ps = ProjectState(project_id="test", root_path="/test")
    assert ps.total_entities == 0
    assert ps.git_head is None
    assert ps.files == {}


def test_search_result():
    e = CodeEntity(
        name="greet",
        file_path="/test.py",
        code="pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
    )
    sr = SearchResult(entity=e, similarity=0.95)
    assert sr.similarity == 0.95
    assert sr.entity.name == "greet"


def test_search_context():
    ctx = SearchContext()
    assert ctx.function is None
    assert ctx.callees == []
    assert ctx.callers == []
    assert ctx.same_file == []
