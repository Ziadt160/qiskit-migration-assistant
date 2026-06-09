"""Extract the Qiskit API symbols a snippet of code actually touches.

For a migration assistant the query is *code*, not a question — so retrieval and
deprecation lookup are driven by the real API surface: imported names, attribute
chains, and call targets, resolved to fully-qualified symbols where possible
(e.g. `from qiskit import execute; execute(...)` -> `qiskit.execute`).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class ExtractedSymbols:
    imports: set[str] = field(default_factory=set)  # imported module/name paths
    qualified: set[str] = field(default_factory=set)  # resolved dotted symbols used
    attributes: set[str] = field(default_factory=set)  # attribute-access chains
    calls: set[str] = field(default_factory=set)  # called names (last segment)

    @property
    def all(self) -> set[str]:
        return set().union(self.imports, self.qualified, self.attributes, self.calls)

    @property
    def lookup_keys(self) -> set[str]:
        """Candidate keys for deprecation matching.

        For each symbol we add: the full symbol, its last segment (to catch method
        records like ``bind_parameters``), and its multi-component module prefixes
        (so ``from qiskit.opflow import X`` -> ``qiskit.opflow.X`` still matches the
        module-level deprecation ``qiskit.opflow``). The bare top-level token
        (``qiskit``) is intentionally excluded as too generic.
        """
        keys: set[str] = set()
        for sym in self.all:
            keys.add(sym)
            if "." in sym:
                parts = sym.split(".")
                keys.add(parts[-1])
                for i in range(2, len(parts)):
                    keys.add(".".join(parts[:i]))
        return {k for k in keys if k}


def _dotted(node: ast.AST) -> str | None:
    """Flatten an attribute/name chain (``a.b.c``) into a dotted string."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.result = ExtractedSymbols()
        # local alias -> fully-qualified import path
        self.alias_map: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.result.imports.add(alias.name)
            self.alias_map[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.result.imports.add(qualified)
            self.alias_map[alias.asname or alias.name] = qualified
        self.generic_visit(node)

    def _resolve(self, dotted: str) -> str:
        head, _, tail = dotted.partition(".")
        if head in self.alias_map:
            base = self.alias_map[head]
            return f"{base}.{tail}" if tail else base
        return dotted

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted = _dotted(node)
        if dotted:
            self.result.attributes.add(self._resolve(dotted))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            self.result.calls.add(func.id)
            if func.id in self.alias_map:
                self.result.qualified.add(self.alias_map[func.id])
        elif isinstance(func, ast.Attribute):
            self.result.calls.add(func.attr)
            dotted = _dotted(func)
            if dotted:
                self.result.qualified.add(self._resolve(dotted))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.alias_map:
            self.result.qualified.add(self.alias_map[node.id])
        self.generic_visit(node)


def extract_symbols(code: str) -> ExtractedSymbols:
    """Parse `code` and return the Qiskit-relevant symbols it references.

    Raises `SyntaxError` if `code` is not valid Python — callers should validate
    input first (see `src.migration.validate_input`).
    """
    tree = ast.parse(code)
    visitor = _SymbolVisitor()
    visitor.visit(tree)
    return visitor.result
