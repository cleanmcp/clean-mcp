"""Tests for Python parser."""

import os

from clean.core.types import EntityKind, Language
from clean.parsing.python import PythonParser

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures")


def test_parse_functions():
    parser = PythonParser()
    path = os.path.join(FIXTURES, "sample.py")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    names = [e.name for e in entities]

    assert "greet" in names
    assert "format_greeting" in names


def test_parse_class_and_methods():
    parser = PythonParser()
    path = os.path.join(FIXTURES, "sample.py")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    names = [e.name for e in entities]

    assert "UserService" in names
    assert "UserService.get_user" in names
    assert "UserService.fetch_data" in names
    assert "UserService.validate" in names


def test_function_calls_extracted():
    parser = PythonParser()
    path = os.path.join(FIXTURES, "sample.py")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    greet = next(e for e in entities if e.name == "greet")
    assert "format_greeting" in greet.calls


def test_method_calls_extracted():
    parser = PythonParser()
    path = os.path.join(FIXTURES, "sample.py")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    get_user = next(e for e in entities if e.name == "UserService.get_user")
    assert "self.fetch_data" in get_user.calls
    assert "self.validate" in get_user.calls


def test_entity_metadata():
    parser = PythonParser()
    path = os.path.join(FIXTURES, "sample.py")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    greet = next(e for e in entities if e.name == "greet")

    assert greet.language == Language.PYTHON
    assert greet.kind == EntityKind.FUNCTION
    assert greet.line_start > 0
    assert greet.line_end >= greet.line_start
    assert greet.file_path == path
    assert "def greet" in greet.code


def test_class_entity_kind():
    parser = PythonParser()
    path = os.path.join(FIXTURES, "sample.py")
    with open(path, "rb") as f:
        source = f.read()

    entities = parser.parse_file(path, source)
    cls = next(e for e in entities if e.name == "UserService")
    assert cls.kind == EntityKind.CLASS

    method = next(e for e in entities if e.name == "UserService.get_user")
    assert method.kind == EntityKind.METHOD
    assert method.class_name == "UserService"
