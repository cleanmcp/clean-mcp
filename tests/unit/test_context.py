"""Tests for ContextExpander."""

import tempfile

import pytest

from clean.core.config import StorageConfig
from clean.core.models import CodeEntity
from clean.core.types import EntityKind, Language
from clean.search.context import ContextExpander
from clean.storage.lancedb import LanceDBStore


@pytest.fixture
def store_with_entities():
    with tempfile.TemporaryDirectory() as tmp:
        config = StorageConfig(default_persist_dir=tmp)
        store = LanceDBStore(config)
        pid = "test"
        store.initialize(pid, 384)

        entities = [
            CodeEntity(
                name="main",
                file_path="/test/main.py",
                code="def main(): greet()",
                line_start=1,
                line_end=1,
                language=Language.PYTHON,
                kind=EntityKind.FUNCTION,
                calls=("greet",),
                called_by=(),
                embedding=[0.1] * 384,
            ),
            CodeEntity(
                name="greet",
                file_path="/test/utils.py",
                code="def greet(): format()",
                line_start=1,
                line_end=1,
                language=Language.PYTHON,
                kind=EntityKind.FUNCTION,
                calls=("format",),
                called_by=("main",),
                embedding=[0.2] * 384,
            ),
            CodeEntity(
                name="format",
                file_path="/test/utils.py",
                code="def format(): pass",
                line_start=5,
                line_end=5,
                language=Language.PYTHON,
                kind=EntityKind.FUNCTION,
                calls=(),
                called_by=("greet",),
                embedding=[0.3] * 384,
            ),
        ]

        store.upsert(pid, entities)
        yield store, pid


def test_expand_callees(store_with_entities):
    store, pid = store_with_entities
    expander = ContextExpander(store)
    ctx = expander.expand(pid, "greet", depth=1)

    assert ctx.function is not None
    assert ctx.function.name == "greet"
    callee_names = [e.name for e in ctx.callees]
    assert "format" in callee_names


def test_expand_callers(store_with_entities):
    store, pid = store_with_entities
    expander = ContextExpander(store)
    ctx = expander.expand(pid, "greet", depth=1)

    caller_names = [e.name for e in ctx.callers]
    assert "main" in caller_names


def test_expand_same_file(store_with_entities):
    store, pid = store_with_entities
    expander = ContextExpander(store)
    ctx = expander.expand(pid, "greet", depth=1)

    same_file_names = [e.name for e in ctx.same_file]
    assert "format" in same_file_names


def test_expand_depth_2_callees(store_with_entities):
    """Depth 2 should follow main→greet→format in batch queries."""
    store, pid = store_with_entities
    expander = ContextExpander(store)
    ctx = expander.expand(pid, "main", depth=2)

    assert ctx.function is not None
    assert ctx.function.name == "main"
    callee_names = [e.name for e in ctx.callees]
    assert "greet" in callee_names
    assert "format" in callee_names


def test_expand_depth_1_stops_at_first_hop(store_with_entities):
    """Depth 1 should only get direct callees, not transitive ones."""
    store, pid = store_with_entities
    expander = ContextExpander(store)
    ctx = expander.expand(pid, "main", depth=1)

    callee_names = [e.name for e in ctx.callees]
    assert "greet" in callee_names
    assert "format" not in callee_names


def test_expand_unknown_function(store_with_entities):
    store, pid = store_with_entities
    expander = ContextExpander(store)
    ctx = expander.expand(pid, "nonexistent", depth=1)

    assert ctx.function is None
    assert ctx.callees == []
    assert ctx.callers == []
