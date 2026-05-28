"""TypeScript/TSX language parser — extends JavaScript parser."""

from __future__ import annotations

import tree_sitter_typescript as tstypescript
from tree_sitter import Language as TSLanguage

from ..core.models import CodeEntity
from ..core.types import Language
from .base import BaseLanguageParser
from .javascript import _find_js_entities

TS_LANGUAGE = TSLanguage(tstypescript.language_typescript())
TSX_LANGUAGE = TSLanguage(tstypescript.language_tsx())


class TypeScriptParser(BaseLanguageParser):
    language = Language.TYPESCRIPT
    extensions = [".ts"]

    def __init__(self) -> None:
        super().__init__(TS_LANGUAGE)

    def _extract_entities(
        self, root_node, source: bytes, file_path: str
    ) -> list[CodeEntity]:
        return _find_js_entities(root_node, source, file_path, self.language)


class TSXParser(BaseLanguageParser):
    language = Language.TYPESCRIPT
    extensions = [".tsx"]

    def __init__(self) -> None:
        super().__init__(TSX_LANGUAGE)

    def _extract_entities(
        self, root_node, source: bytes, file_path: str
    ) -> list[CodeEntity]:
        return _find_js_entities(root_node, source, file_path, self.language)
