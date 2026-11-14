"""The reason-code taxonomy is one source of truth, and it cannot drift.

Three things must stay true, and each failure mode here is silent in production:

1. Every code the code base can emit has exactly one row in ``ezchain_core.reason_codes``.
2. Every row appears in ``docs/REASON-CODES.md``.
3. The older registries (``RISK_REASON_MESSAGES``, ``VAULT_REASONS``, ``BINDING_REASONS``)
   are derived from the taxonomy instead of keeping a private copy.

The scan is deliberately syntactic: it walks the Python AST for the shapes this project
emits codes through (a ``code`` class attribute, a ``RiskReason(...)``, a helper's code
argument, a ``{"code": "X"}`` body, a ``.code``/``.reason`` attribute assignment, and a
constant name resolved to its literal). It is not a grep, so a comment cannot invent a
code and every emission shape is seen. A name assigned its own spelling
(``VAULT_LOCKED = "VAULT_LOCKED"``) is the definition of a constant, not an emission.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TypeGuard

import pytest

from ezchain_core.reason_codes import (
    BINDING_REASONS,
    CATEGORIES,
    EXECUTION_STAGES,
    REASON_CODES,
    RISK_REASON_MESSAGES,
    VAULT_REASONS,
    message_for,
    reason,
)

ROOT = Path(__file__).resolve().parents[1]

# Every directory that may emit a code. Tests are excluded on purpose: a test asserting a
# code is a consumer, not a producer, and would otherwise need a taxonomy row of its own.
SOURCE_ROOTS = (
    "packages/core-py/src",
    "packages/adapters/src",
    "packages/sdk-py/src",
    "apps/api/src",
    "services/engine/src",
    "services/executor/src",
)

DOC = ROOT / "docs" / "REASON-CODES.md"

CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
CODE_KEYWORDS = {"code", "reason", "error_code"}

# Helper -> positional index of the code argument. A helper is a function in this code
# base whose Nth argument is the code it records.
HELPER_CODE_INDEX = {
    "_reject_new": 2,
    "_reject_after_quote": 2,
    "_mark_rejected": 2,
    "_order_failed": 2,
    "_hold": 1,
    "_alert": 1,
    "_issue": 1,
    "_buy": 2,
    "_sell": 1,
}

# Constructor -> positional index of the code argument. ``None`` means keyword-only.
CONSTRUCTOR_CODE_INDEX = {
    "RiskReason": 0,
    "Issue": 0,
    "VaultRefused": 0,
    "ManifestError": 0,
    "ParamsError": 0,
    "StrategyValidationError": 0,
    "ProtectionValidationError": 0,
    "ApiKeyError": 0,
    "ProviderKeyError": 0,
    "PluginIssue": None,
    "PluginTick": None,
    "ArmEvaluation": 1,
    "StrategyDecision": 2,
    "GraphDecision": 3,
    # ``super().__init__(VAULT_LOCKED, message)`` in the executor's signer errors.
    "__init__": 0,
}

CONTAINER_MARKERS = ("REASON", "CODES", "MESSAGES")
REASON_FUNCTION = re.compile(r"reason", re.IGNORECASE)


def _is_code(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and len(value) > 3 and CODE_PATTERN.match(value) is not None


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _collect_constants() -> dict[str, str]:
    """Module-level names whose value is a code, so an emission by name resolves.

    ``TRANSACTION_UNDECODABLE = "TRANSACTION_UNDECODABLE"`` defines a name and emits
    nothing. Resolving the name lets ``RiskReason(TRANSACTION_UNDECODABLE)`` or
    ``code = TRANSACTION_UNDECODABLE`` count, while the definition itself does not.
    """
    constants: dict[str, str] = {}
    for root in SOURCE_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    targets: list[ast.expr] = list(node.targets)
                    value: ast.expr | None = node.value
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                    value = node.value
                else:
                    continue
                if isinstance(value, ast.Constant) and _is_code(value.value):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            constants[target.id] = value.value
    return constants


class _CodeScanner(ast.NodeVisitor):
    """Collect every literal this module can put into a ``code``/``reason`` slot."""

    def __init__(self, constants: dict[str, str] | None = None) -> None:
        self.hits: list[tuple[str, int, str]] = []
        self._functions: list[str] = []
        self._constants = constants or {}

    def _record(self, node: ast.AST, value: object, how: str) -> None:
        if _is_code(value):
            self.hits.append((str(value), getattr(node, "lineno", 0), how))

    def _literal(self, node: ast.expr | None) -> object:
        """The code a node stands for: a literal, or a known constant's value."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self._constants.get(node.id)
        return None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for body in node.body:
            if isinstance(body, (ast.Assign, ast.AnnAssign)):
                targets = body.targets if isinstance(body, ast.Assign) else [body.target]
                if any(isinstance(t, ast.Name) and t.id == "code" for t in targets):
                    value = self._literal(body.value)
                    if value is not None:
                        self._record(body, value, f"class {node.name}.code")
        self._functions.append(f"<class {node.name}>")
        self.generic_visit(node)
        self._functions.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = self._literal(node.value)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in CODE_KEYWORDS:
                    self._record(node, value, f"{target.attr} =")
            # ``VAULT_LOCKED = "VAULT_LOCKED"`` defines the name; only a use of it emits.
            defined_here = isinstance(node.value, ast.Constant) and value in names
            if not defined_here:
                if any(name.startswith(("REASON_", "CODE_")) for name in names):
                    self._record(node, value, "named reason")
                elif any(name in CODE_KEYWORDS for name in names):
                    self._record(node, value, f"{names[0]} =")
        if names and any(marker in names[0] for marker in CONTAINER_MARKERS):
            literals: list[object] = []
            if isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                literals = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
            if isinstance(node.value, ast.Dict):
                literals = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
            for literal in literals:
                self._record(node, literal, f"container {names[0]}")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=False):
            if isinstance(key, ast.Constant) and key.value in CODE_KEYWORDS:
                literal = self._literal(value)
                if literal is not None:
                    self._record(node, literal, f"dict {key.value}=")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = _call_name(node.func)
        for keyword in node.keywords:
            if keyword.arg in CODE_KEYWORDS:
                literal = self._literal(keyword.value)
                if literal is not None:
                    self._record(node, literal, f"{function}({keyword.arg}=)")
        for table in (HELPER_CODE_INDEX, CONSTRUCTOR_CODE_INDEX):
            index = table.get(function)
            if index is not None and len(node.args) > index:
                literal = self._literal(node.args[index])
                if literal is not None:
                    self._record(node, literal, f"{function}(pos)")
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("append", "add"):
            owner = _call_name(node.func.value)
            if owner.endswith(("reasons", "unknown", "codes")) and node.args:
                literal = self._literal(node.args[0])
                if literal is not None:
                    self._record(node, literal, f"{owner}.{node.func.attr}()")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        exception = node.exc
        if isinstance(exception, ast.Call) and exception.args:
            literal = self._literal(exception.args[0])
            if literal is not None:
                self._record(node, literal, f"raise {_call_name(exception.func)}")
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        current = self._functions[-1] if self._functions else ""
        value = node.value
        if isinstance(value, ast.Tuple):
            # ``return wallet, "SOME_CODE"`` — the reason travels beside the value.
            for element in value.elts:
                literal = self._literal(element)
                if literal is not None:
                    self._record(node, literal, f"tuple return in {current}()")
        if isinstance(value, (ast.Constant, ast.Name)):
            literal = self._literal(value)
            if literal is not None:
                self._record(node, literal, f"return in {current}()")
        elif value is not None and REASON_FUNCTION.search(current):
            if isinstance(value, ast.IfExp):
                for branch in (value.body, value.orelse):
                    literal = self._literal(branch)
                    if literal is not None:
                        self._record(node, literal, f"return in {current}()")
            elif isinstance(value, ast.Call):
                for argument in value.args:
                    literal = self._literal(argument)
                    if literal is not None:
                        self._record(node, literal, f"return in {current}()")
                for keyword in value.keywords:
                    if keyword.arg in CODE_KEYWORDS:
                        literal = self._literal(keyword.value)
                        if literal is not None:
                            self._record(node, literal, f"return in {current}()")
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        if node.values:
            head = node.values[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                self._record(node, head.value.rstrip(": "), "f-string prefix")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if isinstance(node.op, ast.Or):
            for operand in node.values[1:]:
                literal = self._literal(operand)
                if literal is not None:
                    self._record(node, literal, "fallback")
        self.generic_visit(node)


def _emitted_codes() -> dict[str, set[str]]:
    """Every code this repository can emit, mapped to where it was found."""
    constants = _collect_constants()
    sites: dict[str, set[str]] = {}
    for root in SOURCE_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            scanner = _CodeScanner(constants)
            scanner.visit(ast.parse(path.read_text(encoding="utf-8")))
            for code, line, how in scanner.hits:
                sites.setdefault(code, set()).add(f"{path.relative_to(ROOT)}:{line} {how}")
    return sites


def _documented_codes() -> set[str]:
    """Codes that own a table row in the taxonomy document."""
    documented: set[str] = set()
    for line in DOC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| `([A-Z][A-Z0-9_]*)` \|", line)
        if match is not None:
            documented.add(match.group(1))
    return documented


def test_the_document_exists() -> None:
    assert DOC.is_file(), "docs/REASON-CODES.md is the public taxonomy and must exist"


def test_every_emitted_code_is_in_the_taxonomy() -> None:
    emitted = _emitted_codes()
    unregistered = sorted(set(emitted) - set(REASON_CODES))

    assert not unregistered, "these codes are emitted but have no taxonomy row: " + ", ".join(
        f"{code} ({sorted(emitted[code])[0]})" for code in unregistered
    )


def test_every_taxonomy_code_is_emitted_somewhere() -> None:
    emitted = _emitted_codes()
    invented = sorted(set(REASON_CODES) - set(emitted))

    assert not invented, (
        "these rows name a code nothing emits; the taxonomy would be promising a failure "
        "that cannot happen: " + ", ".join(invented)
    )


def test_every_taxonomy_code_is_documented() -> None:
    documented = _documented_codes()
    missing = sorted(set(REASON_CODES) - documented)

    assert not missing, "these codes have no row in docs/REASON-CODES.md: " + ", ".join(missing)


def test_the_document_lists_no_unknown_code() -> None:
    documented = _documented_codes()
    unknown = sorted(documented - set(REASON_CODES))

    assert not unknown, "the document lists codes the registry does not define: " + ", ".join(
        unknown
    )


def test_the_derived_registries_are_subsets_of_the_taxonomy() -> None:
    for name, codes in (
        ("RISK_REASON_MESSAGES", RISK_REASON_MESSAGES),
        ("VAULT_REASONS", VAULT_REASONS),
        ("BINDING_REASONS", BINDING_REASONS),
    ):
        unknown = sorted(set(codes) - set(REASON_CODES))
        assert not unknown, f"{name} carries codes the taxonomy does not define: {unknown}"


def test_risk_messages_come_from_the_taxonomy() -> None:
    for code, message in RISK_REASON_MESSAGES.items():
        assert message == REASON_CODES[code].message


def test_vault_and_binding_reasons_have_their_own_category() -> None:
    for code in VAULT_REASONS:
        assert REASON_CODES[code].category == "custody"
    for code in BINDING_REASONS:
        assert REASON_CODES[code].category == "signing"


def test_execution_stages_are_not_reason_codes() -> None:
    """A stage says how far the pipeline got; a code says why it stopped. Never merge."""
    assert not set(EXECUTION_STAGES) & set(REASON_CODES)


def test_every_spec_is_well_formed() -> None:
    allowed_status = {400, 401, 403, 404, 409, 422, 429, 500, 502, 503}
    for spec in REASON_CODES.values():
        assert spec.code == spec.code.upper()
        assert CODE_PATTERN.match(spec.code), spec.code
        assert spec.category in CATEGORIES
        assert spec.severity in ("info", "warning", "critical")
        assert spec.http_status is None or spec.http_status in allowed_status
        assert spec.message and spec.message != spec.code, (
            f"{spec.code} must have real display text, not an echo of its code"
        )
        assert spec.summary.endswith("."), f"{spec.code} needs an operator summary"
        assert spec.source.endswith(".py"), f"{spec.code} must name its source module"


def test_an_unknown_code_is_never_silent() -> None:
    assert message_for("NOT_A_REAL_CODE") == "NOT_A_REAL_CODE"
    with pytest.raises(KeyError):
        reason("NOT_A_REAL_CODE")


def test_the_counts_in_the_document_are_current() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"**Scale:** {len(REASON_CODES)} codes" in text
