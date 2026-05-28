"""Enums and type aliases for Clean."""

from __future__ import annotations

from enum import Enum


class EntityKind(str, Enum):
    FUNCTION = "function"
    ARROW_FUNCTION = "arrow_function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    TYPE = "type"
    EXPORT_CONST = "export_const"
    ENUM = "enum"
    FILE_SUMMARY = "file_summary"


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"


class IndexPhase(str, Enum):
    SCANNING = "scanning"
    PARSING = "parsing"
    EMBEDDING = "embedding"
    STORING = "storing"
    COMPUTING_RELATIONS = "computing_relations"
    DONE = "done"
