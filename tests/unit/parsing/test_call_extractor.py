"""Tests for call extractor."""

import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
from tree_sitter import Language as TSLanguage, Parser

from clean.parsing.call_extractor import extract_calls


def _parse_python(code: str):
    parser = Parser(TSLanguage(tspython.language()))
    return parser.parse(code.encode())


def _parse_js(code: str):
    parser = Parser(TSLanguage(tsjavascript.language()))
    return parser.parse(code.encode())


def test_python_simple_call():
    tree = _parse_python("def f():\n    foo()\n    bar()")
    root = tree.root_node
    func = root.children[0]
    body = func.child_by_field_name("body")
    calls = extract_calls(body, b"def f():\n    foo()\n    bar()")
    assert "foo" in calls
    assert "bar" in calls


def test_python_method_call():
    code = "def f():\n    self.method()\n    obj.do_thing()"
    tree = _parse_python(code)
    func = tree.root_node.children[0]
    body = func.child_by_field_name("body")
    calls = extract_calls(body, code.encode())
    assert "self.method" in calls
    assert "obj.do_thing" in calls


def test_js_function_call():
    code = "function f() { foo(); bar(); }"
    tree = _parse_js(code)
    func = tree.root_node.children[0]
    body = func.child_by_field_name("body")
    calls = extract_calls(body, code.encode())
    assert "foo" in calls
    assert "bar" in calls


def test_js_method_call():
    code = "function f() { this.method(); obj.doThing(); }"
    tree = _parse_js(code)
    func = tree.root_node.children[0]
    body = func.child_by_field_name("body")
    calls = extract_calls(body, code.encode())
    assert "this.method" in calls
    assert "obj.doThing" in calls


def test_deduplication():
    code = "def f():\n    foo()\n    foo()\n    foo()"
    tree = _parse_python(code)
    func = tree.root_node.children[0]
    body = func.child_by_field_name("body")
    calls = extract_calls(body, code.encode())
    assert calls.count("foo") == 1


def test_none_node():
    calls = extract_calls(None, b"")
    assert calls == []
