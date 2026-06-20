"""Extract the Qiskit API symbols a snippet of code actually touches.

For a migration assistant the query is *code*, not a question — so retrieval and
deprecation lookup are driven by the real API surface: imported names, attribute
chains, and call targets, resolved to fully-qualified symbols where possible
(e.g. `from qiskit import execute; execute(...)` -> `qiskit.execute`).

Identity vs. name: a deprecation is a property of a *fully-qualified symbol*, not a
bare name. So we track which usages resolved to a real import (`resolved`) and only
emit a bare last-segment lookup key for usages that did NOT — i.e. a method/attribute
on an object we can't statically type (`qc.bind_parameters()`). That keeps method-record
detection while preventing cross-module last-segment collisions: `from qiskit_aer import
Aer; Aer.get_backend()` resolves to `qiskit_aer.Aer` and must NOT match the removed
`qiskit.Aer` just because both end in `Aer`.
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
    resolved: set[str] = field(
        default_factory=set
    )  # symbols rooted in a real import (full identity)

    @property
    def all(self) -> set[str]:
        return set().union(self.imports, self.qualified, self.attributes, self.calls)

    @property
    def lookup_keys(self) -> set[str]:
        """Candidate keys for deprecation matching.

        For each symbol we add the full symbol and its multi-component module prefixes
        (so ``from qiskit.opflow import X`` -> ``qiskit.opflow.X`` still matches the
        module-level deprecation ``qiskit.opflow``). The bare **last segment** is added
        ONLY for symbols that did *not* resolve to a real import — i.e. method/attribute
        usage on an object we couldn't statically type (``qc.bind_parameters()`` ->
        ``bind_parameters``). Import-resolved symbols match by full identity only, so a
        current API never collides with a removed one that shares a last segment. The
        bare top-level token (``qiskit``) is intentionally excluded as too generic.
        """
        keys: set[str] = set()
        for sym in self.all:
            keys.add(sym)
            if "." in sym:
                parts = sym.split(".")
                for i in range(2, len(parts)):
                    keys.add(".".join(parts[:i]))
                if sym not in self.resolved:  # last segment only for unqualified usages
                    keys.add(parts[-1])
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
            self.result.resolved.add(alias.name)  # an import path is a full identity
            self.alias_map[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.result.imports.add(qualified)
            self.result.resolved.add(qualified)
            self.alias_map[alias.asname or alias.name] = qualified
        self.generic_visit(node)

    def _is_import_rooted(self, dotted: str) -> bool:
        return dotted.partition(".")[0] in self.alias_map

    def _resolve(self, dotted: str) -> str:
        """Resolve a chain against the import alias map; record import-rooted resolutions."""
        head, _, tail = dotted.partition(".")
        if head in self.alias_map:
            base = self.alias_map[head]
            resolved = f"{base}.{tail}" if tail else base
            self.result.resolved.add(resolved)
            return resolved
        return dotted

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted = _dotted(node)
        if dotted:
            self.result.attributes.add(self._resolve(dotted))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in self.alias_map:
                # A call to an imported name (execute, VQE) — match by full identity, not bare name.
                self.result.qualified.add(self._resolve(func.id))
            else:
                self.result.calls.add(func.id)  # bare call, not imported -> last-segment evidence
        elif isinstance(func, ast.Attribute):
            dotted = _dotted(func)
            if dotted and self._is_import_rooted(dotted):
                # Method on a resolved import (Aer.get_backend) -> full identity only.
                self.result.qualified.add(self._resolve(dotted))
            else:
                # Method on an object we can't statically type (qc.bind_parameters) -> last segment.
                self.result.calls.add(func.attr)
                if dotted:
                    self.result.qualified.add(self._resolve(dotted))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.alias_map:
            self.result.qualified.add(self._resolve(node.id))
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
