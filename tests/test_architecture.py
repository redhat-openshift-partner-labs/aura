from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).parent.parent / "src" / "aura"
PROCESS_MODULE = SOURCE / "process.py"


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_only_the_process_module_imports_subprocess() -> None:
    modules = sorted(SOURCE.rglob("*.py"))

    offenders = [
        str(path.relative_to(SOURCE.parent))
        for path in modules
        if path != PROCESS_MODULE
        and any(
            name == "subprocess" or name.startswith("subprocess.")
            for name in imported_modules(path)
        )
    ]

    assert offenders == []
    assert PROCESS_MODULE in modules


JSON_STORE_MODULE = SOURCE / "json_store.py"
# JSON parsing outside json_store, with why none of it loads a stored record.
JSON_PARSERS_ALLOWED = {
    # `oc` output, not a stored record.
    ("collectors/oc_cli.py", "_read_oc"),
    # Reads only `command_id` of an audit record that failed to load.
    ("cli.py", "_names_another_command"),
}

def is_json_parse(call: ast.Call) -> bool:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id == "from_json"
    if not isinstance(function, ast.Attribute):
        return False
    if function.attr in {"from_json", "model_validate_json"}:
        return True
    return (
        function.attr in {"load", "loads"}
        and isinstance(function.value, ast.Name)
        and function.value.id == "json"
    )


def json_parse_calls(path: Path) -> set[tuple[str, str]]:
    """Return (module, enclosing function) for each JSON parse call."""

    found: set[tuple[str, str]] = set()
    relative = str(path.relative_to(SOURCE))

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call) and is_json_parse(node):
            found.add((relative, function))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(path.read_text(encoding="utf-8")), "<module>")
    return found


def test_only_json_store_parses_stored_records() -> None:
    calls = {
        call
        for path in sorted(SOURCE.rglob("*.py"))
        if path != JSON_STORE_MODULE
        for call in json_parse_calls(path)
    }

    assert calls == JSON_PARSERS_ALLOWED


def test_every_store_loads_through_parse_record() -> None:
    import aura.command_store
    import aura.cli  # noqa: F401  imports every store module
    from aura.json_store import JsonRecordStore, parse_record

    def subclasses(cls: type) -> set[type]:
        return {
            child
            for direct in cls.__subclasses__()
            for child in {direct, *subclasses(direct)}
        }

    stores = subclasses(JsonRecordStore)
    assert stores
    for store in stores:
        assert "load" not in vars(store) and "_parse" not in vars(store), store
    reader = ast.parse(
        Path(aura.command_store.__file__).read_text(encoding="utf-8")
    )
    called = {
        node.func.id
        for node in ast.walk(reader)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert parse_record.__name__ in called
