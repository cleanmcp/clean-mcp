"""Parser registry — maps file extensions to language parsers."""

from __future__ import annotations

from ..core.config import ParserConfig
from .base import BaseLanguageParser
from .python import PythonParser
from .javascript import JavaScriptParser
from .typescript import TypeScriptParser, TSXParser


class ParserRegistry:
    """Maps file extensions to parser instances."""

    def __init__(self, config: ParserConfig | None = None) -> None:
        self._parsers: dict[str, BaseLanguageParser] = {}
        self._config = config or ParserConfig()
        self._register_defaults()

    def _register_defaults(self) -> None:
        py = PythonParser()
        js = JavaScriptParser()
        ts = TypeScriptParser()
        tsx = TSXParser()

        parser_map = {
            "python": py,
            "javascript": js,
            "typescript": ts,
        }

        for ext, lang_name in self._config.extension_languages.items():
            if ext == ".tsx":
                self._parsers[ext] = tsx
            elif lang_name in parser_map:
                self._parsers[ext] = parser_map[lang_name]

    def get_parser(self, extension: str) -> BaseLanguageParser | None:
        """Get a parser for the given file extension."""
        return self._parsers.get(extension)

    def supported_extensions(self) -> list[str]:
        """Return all supported file extensions."""
        return list(self._parsers.keys())

    def register(self, extension: str, parser: BaseLanguageParser) -> None:
        """Register a custom parser for an extension."""
        self._parsers[extension] = parser
