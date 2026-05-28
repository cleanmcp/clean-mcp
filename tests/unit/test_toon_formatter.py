"""Tests for TOON formatter."""

from clean.core.models import CodeEntity, SearchResult
from clean.core.types import EntityKind, Language
from clean.formatting.toon import ToonFormatter
from clean.formatting.json import JsonFormatter


def _make_results():
    entities = [
        CodeEntity(
            name="check_reverse",
            file_path="src/utils.py",
            code="def check_reverse(): pass",
            line_start=1,
            line_end=1,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
        ),
        CodeEntity(
            name="validate_email",
            file_path="src/auth.py",
            code="def validate_email(): pass",
            line_start=5,
            line_end=5,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
        ),
    ]
    return [
        SearchResult(entity=entities[0], similarity=0.92),
        SearchResult(entity=entities[1], similarity=0.87),
    ]


def test_toon_format_basic():
    fmt = ToonFormatter()
    results = _make_results()
    output = fmt.format_results(results)

    assert output.startswith("results")
    assert "check_reverse" in output
    assert "validate_email" in output
    assert "92%" in output


def test_toon_format_empty():
    fmt = ToonFormatter()
    output = fmt.format_results([])
    assert "(empty)" in output


def test_toon_smaller_than_json():
    results = _make_results()
    toon = ToonFormatter().format_results(results)
    json_out = JsonFormatter().format_results(results)

    assert len(toon) < len(json_out), "TOON should be more compact than JSON"


def test_json_format_basic():
    fmt = JsonFormatter()
    results = _make_results()
    output = fmt.format_results(results)

    assert "check_reverse" in output
    assert "validate_email" in output
    assert '"similarity"' in output
