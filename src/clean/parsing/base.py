"""Base language parser with shared tree-sitter logic."""

from __future__ import annotations

from abc import ABC, abstractmethod

from tree_sitter import Language as TSLanguage, Parser

from ..core.models import CodeEntity
from ..core.types import Language


class BaseLanguageParser(ABC):
    """Base class for tree-sitter language parsers."""

    language: Language
    extensions: list[str]

    def __init__(self, ts_language: TSLanguage) -> None:
        self._ts_language = ts_language

    def _create_parser(self) -> Parser:
        return Parser(self._ts_language)

    def parse_file(self, file_path: str, source: bytes) -> list[CodeEntity]:
        """Parse a file and return all extracted entities."""
        parser = self._create_parser()
        tree = parser.parse(source)
        return self._extract_entities(tree.root_node, source, file_path)

    @abstractmethod
    def _extract_entities(
        self, root_node, source: bytes, file_path: str
    ) -> list[CodeEntity]:
        """Extract entities from the parsed AST."""
        ...
