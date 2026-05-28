"""Python language parser using tree-sitter."""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language as TSLanguage

from ..core.models import CodeEntity
from ..core.types import EntityKind, Language
from .base import BaseLanguageParser
from .call_extractor import extract_calls

PY_LANGUAGE = TSLanguage(tspython.language())

MAX_AST_DEPTH = 200


class PythonParser(BaseLanguageParser):
    language = Language.PYTHON
    extensions = [".py"]

    def __init__(self) -> None:
        super().__init__(PY_LANGUAGE)

    def _extract_entities(
        self, root_node, source: bytes, file_path: str
    ) -> list[CodeEntity]:
        return _find_python_entities(root_node, source, file_path)


_ENUM_BASES = frozenset({"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"})


def _find_python_entities(
    node, source: bytes, file_path: str, depth: int = 0
) -> list[CodeEntity]:
    if depth > MAX_AST_DEPTH:
        return []

    entities: list[CodeEntity] = []

    if node.type == "decorated_definition":
        # Extract decorators and the inner definition; do NOT recurse further
        # into this node's children — the function/class is handled here.
        inner = node.child_by_field_name("definition")
        if inner is not None:
            if inner.type == "function_definition":
                entities.append(
                    _extract_function(inner, source, file_path, decorator_node=node)
                )
            elif inner.type == "class_definition":
                entities.extend(_extract_class(inner, source, file_path))
        return entities
    elif node.type == "function_definition":
        entities.append(_extract_function(node, source, file_path))
        return entities
    elif node.type == "class_definition":
        entities.extend(_extract_class(node, source, file_path))
        return entities

    for child in node.children:
        entities.extend(_find_python_entities(child, source, file_path, depth + 1))

    return entities


def _collect_decorators(decorated_node, source: bytes) -> tuple[str, ...]:
    """Collect all decorator strings from a decorated_definition node."""
    decs: list[str] = []
    for child in decorated_node.children:
        if child.type == "decorator":
            decs.append(
                source[child.start_byte : child.end_byte].decode(
                    "utf-8", errors="replace"
                )
            )
    return tuple(decs)


def _extract_function(
    node,
    source: bytes,
    file_path: str,
    decorator_node=None,
) -> CodeEntity:
    name_node = node.child_by_field_name("name")
    name = name_node.text.decode("utf-8", errors="replace") if name_node else "unknown"
    func_code = source[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )
    body = node.child_by_field_name("body")
    calls = tuple(extract_calls(body, source)) if body else ()

    decorators: tuple[str, ...] = ()
    code = func_code
    if decorator_node is not None:
        decorators = _collect_decorators(decorator_node, source)
        if decorators:
            # Prepend decorator text so embedding includes route URLs etc.
            code = "\n".join(decorators) + "\n" + func_code

    return CodeEntity(
        name=name,
        file_path=file_path,
        code=code,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        language=Language.PYTHON,
        kind=EntityKind.FUNCTION,
        calls=calls,
        decorators=decorators,
    )


def _get_class_bases(node) -> list[str]:
    """Return the text of all base class names for a class_definition node."""
    bases: list[str] = []
    # tree-sitter-python: argument_list child holds the superclasses
    for child in node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "identifier":
                    bases.append(arg.text.decode("utf-8", errors="replace"))
    return bases


def _extract_class(node, source: bytes, file_path: str) -> list[CodeEntity]:
    results: list[CodeEntity] = []
    name_node = node.child_by_field_name("name")
    class_name = (
        name_node.text.decode("utf-8", errors="replace") if name_node else "unknown"
    )
    code = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    bases = _get_class_bases(node)
    kind = EntityKind.ENUM if any(b in _ENUM_BASES for b in bases) else EntityKind.CLASS

    results.append(
        CodeEntity(
            name=class_name,
            file_path=file_path,
            code=code,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=Language.PYTHON,
            kind=kind,
        )
    )

    # Only extract methods from plain classes (not enums)
    if kind == EntityKind.CLASS:
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    mn = child.child_by_field_name("name")
                    method_name = (
                        mn.text.decode("utf-8", errors="replace") if mn else "unknown"
                    )
                    method_code = source[child.start_byte : child.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    method_body = child.child_by_field_name("body")
                    calls = (
                        tuple(extract_calls(method_body, source)) if method_body else ()
                    )

                    results.append(
                        CodeEntity(
                            name=f"{class_name}.{method_name}",
                            file_path=file_path,
                            code=method_code,
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            language=Language.PYTHON,
                            kind=EntityKind.METHOD,
                            class_name=class_name,
                            calls=calls,
                        )
                    )

    return results
