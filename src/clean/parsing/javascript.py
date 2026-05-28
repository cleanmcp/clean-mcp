"""JavaScript/JSX language parser using tree-sitter."""

from __future__ import annotations

import tree_sitter_javascript as tsjavascript
from tree_sitter import Language as TSLanguage

from ..core.models import CodeEntity
from ..core.types import EntityKind, Language
from .base import BaseLanguageParser
from .call_extractor import extract_calls

JS_LANGUAGE = TSLanguage(tsjavascript.language())

MAX_AST_DEPTH = 200


class JavaScriptParser(BaseLanguageParser):
    language = Language.JAVASCRIPT
    extensions = [".js", ".jsx", ".mjs", ".cjs"]

    def __init__(self) -> None:
        super().__init__(JS_LANGUAGE)

    def _extract_entities(
        self, root_node, source: bytes, file_path: str
    ) -> list[CodeEntity]:
        return _find_js_entities(root_node, source, file_path, self.language)


def _find_js_entities(
    node, source: bytes, file_path: str, lang: Language, depth: int = 0
) -> list[CodeEntity]:
    """Find all JS/TS functions, classes, and exports."""
    if depth > MAX_AST_DEPTH:
        return []

    entities: list[CodeEntity] = []

    if node.type == "function_declaration":
        entities.append(_extract_function(node, source, file_path, lang))

    elif node.type in ("lexical_declaration", "variable_declaration"):
        entities.extend(
            _extract_var_functions(node, source, file_path, lang, exported=False)
        )

    elif node.type == "class_declaration":
        entities.extend(_extract_class(node, source, file_path, lang))

    elif node.type == "export_statement":
        entities.extend(_extract_export(node, source, file_path, lang))

    elif node.type == "interface_declaration":
        entities.append(
            _extract_interface_or_type(
                node, source, file_path, lang, EntityKind.INTERFACE
            )
        )

    elif node.type == "type_alias_declaration":
        entities.append(
            _extract_interface_or_type(node, source, file_path, lang, EntityKind.TYPE)
        )

    elif node.type == "enum_declaration":
        entities.append(_extract_enum(node, source, file_path, lang, exported=False))

    else:
        for child in node.children:
            entities.extend(
                _find_js_entities(child, source, file_path, lang, depth + 1)
            )

    return entities


def _extract_function(
    node, source: bytes, file_path: str, lang: Language
) -> CodeEntity:
    name_node = node.child_by_field_name("name")
    name = (
        name_node.text.decode("utf-8", errors="replace") if name_node else "anonymous"
    )
    code = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    body = node.child_by_field_name("body")
    calls = tuple(extract_calls(body, source)) if body else ()

    return CodeEntity(
        name=name,
        file_path=file_path,
        code=code,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        language=lang,
        kind=EntityKind.FUNCTION,
        calls=calls,
    )


def _extract_var_functions(
    node,
    source: bytes,
    file_path: str,
    lang: Language,
    exported: bool,
) -> list[CodeEntity]:
    """Extract arrow functions or function expressions from variable declarations."""
    entities: list[CodeEntity] = []

    for child in node.children:
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        value_node = child.child_by_field_name("value")
        var_name = (
            name_node.text.decode("utf-8", errors="replace") if name_node else None
        )

        if value_node and value_node.type == "arrow_function":
            body = value_node.child_by_field_name("body")
            calls = tuple(extract_calls(body, source)) if body else ()
            code = source[node.start_byte : node.end_byte].decode(
                "utf-8", errors="replace"
            )
            entities.append(
                CodeEntity(
                    name=var_name or "anonymous",
                    file_path=file_path,
                    code=code,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    language=lang,
                    kind=EntityKind.ARROW_FUNCTION,
                    calls=calls,
                    exported=exported,
                )
            )
        elif value_node and value_node.type == "function":
            body = value_node.child_by_field_name("body")
            calls = tuple(extract_calls(body, source)) if body else ()
            code = source[node.start_byte : node.end_byte].decode(
                "utf-8", errors="replace"
            )
            entities.append(
                CodeEntity(
                    name=var_name or "anonymous",
                    file_path=file_path,
                    code=code,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    language=lang,
                    kind=EntityKind.FUNCTION,
                    calls=calls,
                    exported=exported,
                )
            )

    return entities


def _extract_class(
    node, source: bytes, file_path: str, lang: Language
) -> list[CodeEntity]:
    results: list[CodeEntity] = []
    name_node = node.child_by_field_name("name")
    class_name = (
        name_node.text.decode("utf-8", errors="replace") if name_node else "unknown"
    )
    code = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    results.append(
        CodeEntity(
            name=class_name,
            file_path=file_path,
            code=code,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=lang,
            kind=EntityKind.CLASS,
        )
    )

    body = node.child_by_field_name("body")
    if body:
        for child in body.children:
            if child.type in ("method_definition", "public_field_definition"):
                mn = child.child_by_field_name("name")
                method_name = (
                    mn.text.decode("utf-8", errors="replace") if mn else "unknown"
                )
                method_code = source[child.start_byte : child.end_byte].decode(
                    "utf-8", errors="replace"
                )
                method_body = child.child_by_field_name("body")
                calls = tuple(extract_calls(method_body, source)) if method_body else ()

                results.append(
                    CodeEntity(
                        name=f"{class_name}.{method_name}",
                        file_path=file_path,
                        code=method_code,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        language=lang,
                        kind=EntityKind.METHOD,
                        class_name=class_name,
                        calls=calls,
                    )
                )

    return results


def _extract_export(
    node, source: bytes, file_path: str, lang: Language
) -> list[CodeEntity]:
    """Extract entities from export statements."""
    entities: list[CodeEntity] = []
    declaration = node.child_by_field_name("declaration")

    if declaration:
        if declaration.type == "function_declaration":
            e = _extract_function(declaration, source, file_path, lang)
            code = source[node.start_byte : node.end_byte].decode(
                "utf-8", errors="replace"
            )
            entities.append(
                CodeEntity(
                    id=e.id,
                    name=e.name,
                    file_path=e.file_path,
                    code=code,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    language=lang,
                    kind=e.kind,
                    calls=e.calls,
                    exported=True,
                )
            )
        elif declaration.type == "class_declaration":
            class_entities = _extract_class(declaration, source, file_path, lang)
            for e in class_entities:
                code = (
                    e.code
                    if e.kind != EntityKind.CLASS
                    else source[node.start_byte : node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                )
                entities.append(
                    CodeEntity(
                        id=e.id,
                        name=e.name,
                        file_path=e.file_path,
                        code=code,
                        line_start=e.line_start,
                        line_end=e.line_end,
                        language=lang,
                        kind=e.kind,
                        calls=e.calls,
                        class_name=e.class_name,
                        exported=True,
                    )
                )
        elif declaration.type in ("lexical_declaration", "variable_declaration"):
            entities.extend(
                _extract_var_functions(
                    declaration, source, file_path, lang, exported=True
                )
            )
            # Fix: wrap with export code for var functions
            for i, e in enumerate(entities):
                if not e.exported:
                    continue
                code = source[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                entities[i] = CodeEntity(
                    id=e.id,
                    name=e.name,
                    file_path=e.file_path,
                    code=code,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    language=lang,
                    kind=e.kind,
                    calls=e.calls,
                    exported=True,
                )
            # Also extract non-function exported consts
            entities.extend(
                _extract_export_consts(declaration, source, file_path, lang, node)
            )
        elif declaration.type == "enum_declaration":
            entities.append(
                _extract_enum(declaration, source, file_path, lang, exported=True)
            )
        elif declaration.type == "interface_declaration":
            e = _extract_interface_or_type(
                declaration, source, file_path, lang, EntityKind.INTERFACE
            )
            entities.append(
                CodeEntity(
                    id=e.id,
                    name=e.name,
                    file_path=e.file_path,
                    code=e.code,
                    line_start=e.line_start,
                    line_end=e.line_end,
                    language=lang,
                    kind=e.kind,
                    exported=True,
                )
            )
        elif declaration.type == "type_alias_declaration":
            e = _extract_interface_or_type(
                declaration, source, file_path, lang, EntityKind.TYPE
            )
            entities.append(
                CodeEntity(
                    id=e.id,
                    name=e.name,
                    file_path=e.file_path,
                    code=e.code,
                    line_start=e.line_start,
                    line_end=e.line_end,
                    language=lang,
                    kind=e.kind,
                    exported=True,
                )
            )
    else:
        # Default exports or named re-exports
        for child in node.children:
            if child.type == "function_declaration":
                e = _extract_function(child, source, file_path, lang)
                entities.append(
                    CodeEntity(
                        id=e.id,
                        name=e.name,
                        file_path=e.file_path,
                        code=e.code,
                        line_start=e.line_start,
                        line_end=e.line_end,
                        language=lang,
                        kind=e.kind,
                        calls=e.calls,
                        exported=True,
                    )
                )
            elif child.type == "class_declaration":
                for e in _extract_class(child, source, file_path, lang):
                    entities.append(
                        CodeEntity(
                            id=e.id,
                            name=e.name,
                            file_path=e.file_path,
                            code=e.code,
                            line_start=e.line_start,
                            line_end=e.line_end,
                            language=lang,
                            kind=e.kind,
                            calls=e.calls,
                            class_name=e.class_name,
                            exported=True,
                        )
                    )

    return entities


def _extract_interface_or_type(
    node,
    source: bytes,
    file_path: str,
    lang: Language,
    kind: EntityKind,
) -> CodeEntity:
    name_node = node.child_by_field_name("name")
    name = name_node.text.decode("utf-8", errors="replace") if name_node else "unknown"
    code = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    return CodeEntity(
        name=name,
        file_path=file_path,
        code=code,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        language=lang,
        kind=kind,
    )


def _detect_export_const_sub_kind(value_node, source: bytes) -> str | None:
    """Detect the sub_kind of an exported const from its value node."""
    if value_node is None:
        return None

    value_type = value_node.type
    if value_type == "object":
        return "config_object"
    if value_type == "array":
        return "config_array"

    # For call expressions and other constructs, inspect the source text
    value_text = source[value_node.start_byte : value_node.end_byte].decode(
        "utf-8", errors="replace"
    )
    if "z.object(" in value_text or "z.string(" in value_text:
        return "zod_schema"
    if (
        "pgTable(" in value_text
        or "sqliteTable(" in value_text
        or "mysqlTable(" in value_text
    ):
        return "db_table"
    if "createContext(" in value_text:
        return "react_context"

    return None


def _extract_export_consts(
    decl_node,
    source: bytes,
    file_path: str,
    lang: Language,
    export_node,
) -> list[CodeEntity]:
    """Extract non-function exported const assignments from a lexical_declaration.

    Skips variable_declarators whose value is an arrow_function or function —
    those are already handled by ``_extract_var_functions``.
    """
    entities: list[CodeEntity] = []

    for child in decl_node.children:
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        value_node = child.child_by_field_name("value")

        if value_node is None:
            continue
        if value_node.type in ("arrow_function", "function"):
            continue

        var_name = (
            name_node.text.decode("utf-8", errors="replace") if name_node else None
        )
        if not var_name:
            continue

        sub_kind = _detect_export_const_sub_kind(value_node, source)
        code = source[export_node.start_byte : export_node.end_byte].decode(
            "utf-8", errors="replace"
        )

        entities.append(
            CodeEntity(
                name=var_name,
                file_path=file_path,
                code=code,
                line_start=export_node.start_point[0] + 1,
                line_end=export_node.end_point[0] + 1,
                language=lang,
                kind=EntityKind.EXPORT_CONST,
                exported=True,
                sub_kind=sub_kind,
            )
        )

    return entities


def _extract_enum(
    node,
    source: bytes,
    file_path: str,
    lang: Language,
    exported: bool,
) -> CodeEntity:
    """Extract an enum_declaration node."""
    name_node = node.child_by_field_name("name")
    name = name_node.text.decode("utf-8", errors="replace") if name_node else "unknown"
    code = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    return CodeEntity(
        name=name,
        file_path=file_path,
        code=code,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        language=lang,
        kind=EntityKind.ENUM,
        exported=exported,
    )
