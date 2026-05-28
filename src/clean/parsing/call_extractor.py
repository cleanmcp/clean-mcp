"""Language-agnostic function call extraction from tree-sitter AST nodes."""

from __future__ import annotations

MAX_AST_DEPTH = 200


def extract_calls(node, source_code: bytes) -> list[str]:
    """
    Extract all function/method calls from a tree-sitter node.

    Handles JS/TS patterns (functionName(), object.method(), this.method())
    and Python patterns (function(), object.method(), self.method()).

    Returns deduplicated list preserving order.
    """
    calls: list[str] = []
    seen: set[str] = set()

    def _extract_member(node, source_code: bytes) -> str | None:
        """Extract from JS/TS member_expression (object.property)."""
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if obj is None or prop is None:
            return node.text.decode("utf-8", errors="replace")
        if obj.type == "member_expression":
            obj_name = _extract_member(obj, source_code)
        else:
            obj_name = obj.text.decode("utf-8", errors="replace")
        return f"{obj_name}.{prop.text.decode('utf-8', errors='replace')}"

    def _extract_attribute(node, source_code: bytes) -> str | None:
        """Extract from Python attribute (object.attribute)."""
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        if obj is None or attr is None:
            return node.text.decode("utf-8", errors="replace")
        if obj.type == "attribute":
            obj_name = _extract_attribute(obj, source_code)
        else:
            obj_name = obj.text.decode("utf-8", errors="replace")
        return f"{obj_name}.{attr.text.decode('utf-8', errors='replace')}"

    def _extract_call_name(call_node) -> str | None:
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            for child in call_node.children:
                if child.type not in ("arguments", "template_string"):
                    func_node = child
                    break
        if func_node is None:
            return None

        if func_node.type == "identifier":
            return func_node.text.decode("utf-8", errors="replace")
        elif func_node.type == "member_expression":
            return _extract_member(func_node, source_code)
        elif func_node.type == "attribute":
            return _extract_attribute(func_node, source_code)
        return None

    def _traverse(n, depth=0):
        if depth > MAX_AST_DEPTH:
            return
        if n.type in ("call_expression", "call"):
            name = _extract_call_name(n)
            if name and name not in seen:
                seen.add(name)
                calls.append(name)
        for child in n.children:
            _traverse(child, depth + 1)

    if node is not None:
        _traverse(node)
    return calls
