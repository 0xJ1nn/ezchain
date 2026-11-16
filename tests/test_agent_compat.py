"""The public agent/model compatibility table cannot drift from the tests it names.

``docs/AGENT-INTEGRATION.md`` claims a capability for a model client only when a named
test proves it. Three failure modes are silent in a public table: a capability marked as
supported with no test name, a test name that no longer exists, and a new client or column
added without updating the check. This test parses the marked-up table and rejects all
three. It is deliberately syntactic: a test name must be a backticked ``test_*`` token and
must name a function collected under ``tests/``, so prose cannot stand in for evidence.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "AGENT-INTEGRATION.md"
TESTS = ROOT / "tests"
START = "<!-- compatibility-table:start -->"
END = "<!-- compatibility-table:end -->"

HEADER = (
    "Model client",
    "Tool calling",
    "Parallel tool calls",
    "Streaming",
    "Structured final answer",
    "Token accounting",
)
CLIENTS = ("Anthropic Messages", "OpenAI-compatible chat")
NOT_TESTED = "not tested"
TEST_CELL = re.compile(r"`(test_[A-Za-z0-9_]+)`")


def _table() -> list[list[str]]:
    """Return the table's rows, excluding the markdown separator line."""
    text = DOC.read_text(encoding="utf-8")
    assert START in text, f"compatibility table start marker is missing from {DOC}"
    assert END in text, f"compatibility table end marker is missing from {DOC}"
    body = text.split(START, 1)[1].split(END, 1)[0]
    rows: list[list[str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def _known_tests() -> set[str]:
    """Every test function defined under tests/, read as AST rather than imported."""
    names: set[str] = set()
    for path in TESTS.rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    names.add(node.name)
    return names


def _check_shape(rows: list[list[str]]) -> None:
    assert rows, "the compatibility table has no rows"
    assert tuple(rows[0]) == HEADER, "the compatibility table's columns changed"
    data = rows[1:]
    assert len(data) == len(CLIENTS), "the number of model clients changed"
    for row, client in zip(data, CLIENTS, strict=True):
        assert client in row[0], f"unexpected or missing model client: {row[0]!r}"


def _check_claims(rows: list[list[str]], known: set[str]) -> None:
    for row in rows[1:]:
        for cell in row[1:]:
            if cell == NOT_TESTED:
                continue
            match = TEST_CELL.fullmatch(cell)
            assert match is not None, f"capability claimed with no test name: {cell!r}"
            name = match.group(1)
            assert name in known, f"{name} is named in the table but no such test exists"


def test_the_table_names_the_two_implemented_clients_and_all_capabilities() -> None:
    _check_shape(_table())


def test_every_claimed_capability_names_a_test_that_exists() -> None:
    rows = _table()
    _check_claims(rows, _known_tests())
    claimed = sum(cell != NOT_TESTED for row in rows[1:] for cell in row[1:])
    assert claimed > 0, "a table with no claims proves nothing"


def test_a_capability_claimed_without_a_test_name_is_rejected() -> None:
    rows = [list(HEADER), [CLIENTS[0], "supported", *(["not tested"] * 4)]]
    try:
        _check_claims(rows, _known_tests())
    except AssertionError:
        return
    raise AssertionError("a prose claim should have been rejected")


def test_a_test_name_that_does_not_exist_is_rejected() -> None:
    rows = [list(HEADER), [CLIENTS[0], "`test_no_such_test`", *(["not tested"] * 4)]]
    try:
        _check_claims(rows, _known_tests())
    except AssertionError:
        return
    raise AssertionError("a missing test should have been rejected")
