"""A patch check stub for generator tests that do not exercise Git apply."""

from __future__ import annotations


class NoReasonsCheck:
    def check(self, **_: object) -> list[str]:
        return []
