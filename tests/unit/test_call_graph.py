"""Tests for CallGraphBuilder."""

from unittest.mock import MagicMock

import pytest

from clean.core.models import CodeEntity
from clean.core.types import EntityKind, Language
from clean.indexing.call_graph import CallGraphBuilder


def _entity(
    name: str, calls: tuple[str, ...] = (), called_by: tuple[str, ...] = ()
) -> CodeEntity:
    """Helper to create a minimal CodeEntity for testing."""
    return CodeEntity(
        name=name,
        file_path=f"test/{name}.py",
        code=f"def {name}(): pass",
        line_start=1,
        line_end=1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
        calls=calls,
        called_by=called_by,
    )


@pytest.fixture
def builder():
    store = MagicMock()
    return CallGraphBuilder(store)


class TestCallGraphBuilder:
    def test_empty_input(self, builder):
        """Empty entity list returns empty list."""
        assert builder.compute("proj1", []) == []

    def test_no_calls(self, builder):
        """Entities with no calls have empty called_by."""
        entities = [_entity("foo"), _entity("bar")]
        result = builder.compute("proj1", entities)
        assert len(result) == 2
        assert result[0].called_by == ()
        assert result[1].called_by == ()

    def test_simple_call(self, builder):
        """A calls B => B.called_by contains A."""
        a = _entity("a", calls=("b",))
        b = _entity("b")
        result = builder.compute("proj1", [a, b])
        by_name = {e.name: e for e in result}
        assert by_name["b"].called_by == ("a",)
        assert by_name["a"].called_by == ()

    def test_mutual_calls(self, builder):
        """A calls B, B calls A => both have each other in called_by."""
        a = _entity("a", calls=("b",))
        b = _entity("b", calls=("a",))
        result = builder.compute("proj1", [a, b])
        by_name = {e.name: e for e in result}
        assert by_name["a"].called_by == ("b",)
        assert by_name["b"].called_by == ("a",)

    def test_multiple_callers(self, builder):
        """Multiple entities calling the same function."""
        a = _entity("a", calls=("c",))
        b = _entity("b", calls=("c",))
        c = _entity("c")
        result = builder.compute("proj1", [a, b, c])
        by_name = {e.name: e for e in result}
        assert set(by_name["c"].called_by) == {"a", "b"}

    def test_call_to_nonexistent_function(self, builder):
        """Calling a function not in the entity list produces no crash."""
        a = _entity("a", calls=("nonexistent",))
        result = builder.compute("proj1", [a])
        assert len(result) == 1
        assert result[0].called_by == ()

    def test_preserves_existing_called_by_when_unchanged(self, builder):
        """If called_by is already correct, entity object is reused (no copy)."""
        a = _entity("a", calls=("b",))
        b = _entity("b", called_by=("a",))
        result = builder.compute("proj1", [a, b])
        assert result[1] is b

    def test_chain_a_calls_b_calls_c(self, builder):
        """A -> B -> C chain."""
        a = _entity("a", calls=("b",))
        b = _entity("b", calls=("c",))
        c = _entity("c")
        result = builder.compute("proj1", [a, b, c])
        by_name = {e.name: e for e in result}
        assert by_name["a"].called_by == ()
        assert by_name["b"].called_by == ("a",)
        assert by_name["c"].called_by == ("b",)

    def test_self_call(self, builder):
        """Recursive function: A calls A."""
        a = _entity("a", calls=("a",))
        result = builder.compute("proj1", [a])
        assert result[0].called_by == ("a",)

    def test_large_entity_set_performance(self, builder):
        """Verify the algorithm handles 5000 entities without excessive time."""
        import time

        n = 5000
        entities = [
            _entity(f"fn_{i}", calls=(f"fn_{i + 1}",) if i < n - 1 else ())
            for i in range(n)
        ]
        t0 = time.monotonic()
        result = builder.compute("proj1", entities)
        elapsed = time.monotonic() - t0
        assert len(result) == n
        assert elapsed < 1.0, f"Took {elapsed:.2f}s — too slow for {n} entities"
