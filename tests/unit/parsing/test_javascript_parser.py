"""Tests for JavaScript parser."""

import os

from clean.core.types import EntityKind, Language
from clean.parsing.javascript import JavaScriptParser

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures")


def test_parse_function_declaration():
    parser = JavaScriptParser()
    path = os.path.join(FIXTURES, "sample.js")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    names = [e.name for e in entities]

    assert "add" in names


def test_parse_arrow_function():
    parser = JavaScriptParser()
    path = os.path.join(FIXTURES, "sample.js")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    multiply = next((e for e in entities if e.name == "multiply"), None)
    assert multiply is not None
    assert multiply.kind == EntityKind.ARROW_FUNCTION


def test_parse_class():
    parser = JavaScriptParser()
    path = os.path.join(FIXTURES, "sample.js")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    names = [e.name for e in entities]

    assert "Calculator" in names
    assert "Calculator.constructor" in names
    assert "Calculator.calculate" in names


def test_parse_exports():
    parser = JavaScriptParser()
    path = os.path.join(FIXTURES, "sample.js")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    subtract = next((e for e in entities if e.name == "subtract"), None)
    assert subtract is not None
    assert subtract.exported is True

    divide = next((e for e in entities if e.name == "divide"), None)
    assert divide is not None
    assert divide.exported is True


def test_calls_extracted():
    parser = JavaScriptParser()
    path = os.path.join(FIXTURES, "sample.js")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    calc = next((e for e in entities if e.name == "Calculator.calculate"), None)
    assert calc is not None
    assert "add" in calc.calls


def test_language_is_javascript():
    parser = JavaScriptParser()
    path = os.path.join(FIXTURES, "sample.js")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    for e in entities:
        assert e.language == Language.JAVASCRIPT
