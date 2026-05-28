"""Shared test fixtures for Clean tests."""

from __future__ import annotations

import os
import tempfile

import pytest

from clean.core.config import CleanConfig
from clean.core.models import CodeEntity, SearchResult
from clean.core.types import EntityKind, Language

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def sample_py_path():
    return os.path.join(FIXTURES_DIR, "sample.py")


@pytest.fixture
def sample_js_path():
    return os.path.join(FIXTURES_DIR, "sample.js")


@pytest.fixture
def sample_ts_path():
    return os.path.join(FIXTURES_DIR, "sample.ts")


@pytest.fixture
def sample_py_source(sample_py_path):
    with open(sample_py_path, "rb") as f:
        return f.read()


@pytest.fixture
def sample_js_source(sample_js_path):
    with open(sample_js_path, "rb") as f:
        return f.read()


@pytest.fixture
def sample_ts_source(sample_ts_path):
    with open(sample_ts_path, "rb") as f:
        return f.read()


@pytest.fixture
def config():
    return CleanConfig()


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def sample_entity():
    return CodeEntity(
        name="greet",
        file_path="/test/sample.py",
        code='def greet(name):\n    return f"Hello, {name}!"',
        line_start=1,
        line_end=2,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
        calls=("format_greeting",),
    )


@pytest.fixture
def sample_entities():
    return [
        CodeEntity(
            name="greet",
            file_path="/test/sample.py",
            code="def greet(name):\n    return format_greeting(name)",
            line_start=1,
            line_end=2,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
            calls=("format_greeting",),
            embedding=[0.1] * 384,
        ),
        CodeEntity(
            name="format_greeting",
            file_path="/test/sample.py",
            code='def format_greeting(name):\n    return f"Hello, {name}!"',
            line_start=5,
            line_end=6,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
            calls=(),
            embedding=[0.2] * 384,
        ),
        CodeEntity(
            name="process",
            file_path="/test/main.py",
            code='def process():\n    greet("world")',
            line_start=1,
            line_end=2,
            language=Language.PYTHON,
            kind=EntityKind.FUNCTION,
            calls=("greet",),
            embedding=[0.3] * 384,
        ),
    ]


@pytest.fixture
def sample_search_results(sample_entities):
    return [
        SearchResult(entity=sample_entities[0], similarity=0.92),
        SearchResult(entity=sample_entities[1], similarity=0.85),
    ]
