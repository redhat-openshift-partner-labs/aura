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
