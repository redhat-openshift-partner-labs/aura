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


def spawns_a_process(name: str) -> bool:
    return name in {"system", "popen"} or name.startswith(
        ("posix_spawn", "exec", "spawn", "fork")
    )


# Modules that start programs or processes without going through os.
PROCESS_MODULES = {"pty", "multiprocessing", "asyncio.subprocess"}
PROCESS_FUNCTIONS = {
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
}
# What each parser reads, keyed by the module that defines it.
PARSE_FUNCTIONS = {
    ("json", "load"),
    ("json", "loads"),
    ("pydantic_core", "from_json"),
    ("yaml", "safe_load"),
    ("yaml", "safe_load_all"),
    ("yaml", "load"),
    ("yaml", "load_all"),
    ("yaml", "full_load"),
    ("yaml", "unsafe_load"),
}
# Parsing that any object can do, whatever module it comes from.
PARSE_METHODS = {"model_validate_json", "validate_json", "validate_strings"}


def module_bindings(tree: ast.AST) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Return names bound to modules and names bound to module members,
    through any alias."""

    modules: dict[str, str] = {}
    members: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    modules[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                members[alias.asname or alias.name] = (node.module, alias.name)
    return modules, members


def referenced(
    node: ast.AST,
    modules: dict[str, str],
    members: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    """Return (module, member) that a name or attribute refers to."""

    if isinstance(node, ast.Name):
        return members.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        module = modules.get(node.value.id)
        if module is not None:
            return module, node.attr
    return None


def process_calls(path: Path) -> list[str]:
    """Return every reference that can start a program or process."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, members = module_bindings(tree)
    found = [
        f"import {module}"
        for module in {*modules.values(), *(m for m, _ in members.values())}
        if module in PROCESS_MODULES
    ]
    found.extend(
        f"from {module} import {name}"
        for module, name in members.values()
        if (module == "os" and spawns_a_process(name))
        or (module, name) in PROCESS_FUNCTIONS
    )
    for node in ast.walk(tree):
        target = referenced(node, modules, members)
        if target is not None and (
            (target[0] == "os" and spawns_a_process(target[1]))
            or target in PROCESS_FUNCTIONS
        ):
            found.append(".".join(target))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and modules.get(node.args[0].id) == "os"
        ):
            found.append("getattr(os, ...)")
    return found


def test_only_the_process_module_starts_programs() -> None:
    offenders = {
        str(path.relative_to(SOURCE.parent)): calls
        for path in sorted(SOURCE.rglob("*.py"))
        if path != PROCESS_MODULE
        if (calls := process_calls(path))
    }

    assert offenders == {}


JSON_STORE_MODULE = SOURCE / "json_store.py"
# Parsing outside json_store, with why none of it loads a stored record.
PARSERS_ALLOWED = {
    # `oc` output.
    ("collectors/oc_cli.py", "_read_oc"),
    # Reads only `command_id` of an audit record that failed to load.
    ("cli.py", "_names_another_command"),
    # fleet-clusters YAML files and patched file text.
    ("collectors/git_inventory.py", "_load_yaml"),
    ("proposals.py", "_metadata_mapping"),
    ("proposals.py", "_yaml_mapping"),
    ("services/cluster_edit.py", "_mapping"),
    ("services/cluster_edit.py", "_parsed"),
    ("services/rerun.py", "_metadata"),
    ("services/scaffold.py", "_mapping"),
    ("services/scaffold.py", "_secret_fields"),
}


def parse_calls(path: Path) -> set[tuple[str, str]]:
    """Return (module, enclosing function) for each parse reference."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, members = module_bindings(tree)
    found: set[tuple[str, str]] = set()
    relative = str(path.relative_to(SOURCE))

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if referenced(node, modules, members) in PARSE_FUNCTIONS or (
            isinstance(node, ast.Attribute) and node.attr in PARSE_METHODS
        ):
            found.add((relative, function))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return found


def test_only_json_store_parses_stored_records() -> None:
    calls = {
        call
        for path in sorted(SOURCE.rglob("*.py"))
        if path != JSON_STORE_MODULE
        for call in parse_calls(path)
    }

    assert calls == PARSERS_ALLOWED


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


EXECUTION = SOURCE / "execution"
EXECUTE_MODULE = EXECUTION / "execute.py"
# Credentials and the modules that may read them (I10, task 3.6). Stages 4
# and 5 add the adapters' credentials here.
CREDENTIAL_READERS = {
    "password_file": {SOURCE / "config.py", EXECUTION / "store.py"},
}


def adapter_classes(tree: ast.AST) -> list[str]:
    """Classes with a `write` method that takes an AdapterRequest."""

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # The ExecutionAdapter protocol declares the method; it is no adapter.
        if any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases):
            continue
        for item in node.body:
            if (
                isinstance(item, ast.FunctionDef)
                and item.name == "write"
                and any(
                    (isinstance(arg.annotation, ast.Name) and arg.annotation.id == "AdapterRequest")
                    or (
                        isinstance(arg.annotation, ast.Constant)
                        and arg.annotation.value == "AdapterRequest"
                    )
                    for arg in item.args.args
                )
            ):
                found.append(node.name)
    return found


def adapter_write_calls(tree: ast.AST) -> int:
    """Calls of `write` on an object named as an adapter, or that pass an
    adapter request, whatever the object is called."""

    def named(node: ast.AST, word: str) -> bool:
        if isinstance(node, ast.Name):
            return word in node.id.lower()
        if isinstance(node, ast.Attribute):
            return word in node.attr.lower() or named(node.value, word)
        return False

    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write"
        and (
            named(node.func.value, "adapter")
            or any(named(arg, "request") for arg in node.args)
        )
    )


def parsed() -> dict[Path, ast.AST]:
    return {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SOURCE.rglob("*.py"))
    }


def test_adapters_live_in_the_execution_package() -> None:
    misplaced = {
        str(path.relative_to(SOURCE)): classes
        for path, tree in parsed().items()
        if EXECUTION not in path.parents
        if (classes := adapter_classes(tree))
    }

    assert misplaced == {}


def test_only_execute_calls_an_adapter_write() -> None:
    callers = {
        str(path.relative_to(SOURCE)): count
        for path, tree in parsed().items()
        if (count := adapter_write_calls(tree))
    }

    assert callers == {"execution/execute.py": 1}


def test_only_their_modules_read_credentials() -> None:
    for credential, readers in CREDENTIAL_READERS.items():
        reading = {
            path
            for path, tree in parsed().items()
            for node in ast.walk(tree)
            if (isinstance(node, ast.Attribute) and node.attr == credential)
            or (isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == credential)
            # getattr(config.store, "password_file") and the like.
            or (isinstance(node, ast.Constant) and node.value == credential)
        }
        assert reading <= readers, (credential, reading - readers)
