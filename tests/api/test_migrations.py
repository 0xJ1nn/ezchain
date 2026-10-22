"""Migration hygiene: a fresh baseline and a forward-only upgrade path (CONTRACT I8)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ezchain_api.db import head_revision

VERSIONS = Path(__file__).resolve().parents[2] / "apps/api/alembic/versions"
FORBIDDEN_UPGRADE_OPERATIONS = (
    "drop_table",
    "drop_column",
    "truncate",
    "delete from",
    "wipe",
    "clean_slate",
)
BASELINE_TABLES = (
    "ledger_entries",
    "market_snapshots",
    "paper_fills",
    "paper_orders",
    "strategies",
    "strategy_runs",
    "system_flags",
)


def _upgrade_source(source: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("migration has no upgrade() function")


def destructive_upgrade_operations(source: str) -> list[str]:
    """Only ``upgrade()`` is policed; ``downgrade()`` may drop objects."""
    body = _upgrade_source(source).lower()
    return [token for token in FORBIDDEN_UPGRADE_OPERATIONS if token in body]


def test_migration_head_is_the_latest_revision() -> None:
    assert head_revision() == "0004_market_candles"


def test_upgrade_bodies_are_forward_only() -> None:
    files = sorted(VERSIONS.glob("*.py"))
    assert files, "no migration revisions found"
    for path in files:
        found = destructive_upgrade_operations(path.read_text())
        assert not found, f"{path.name}: destructive upgrade operation(s) {found}"


def test_baseline_creates_every_current_table() -> None:
    source = (VERSIONS / "0001_baseline.py").read_text()
    upgrade = _upgrade_source(source)
    missing = [
        table
        for table in BASELINE_TABLES
        if re.search(rf"create_table\(\s*['\"]{re.escape(table)}['\"]", upgrade) is None
    ]
    assert not missing, f"baseline is missing tables: {missing}"


def test_the_guard_detects_a_destructive_upgrade() -> None:
    sample = """
def upgrade():
    op.drop_column("ledger_entries", "asset")

def downgrade():
    op.drop_table("ledger_entries")
"""
    assert destructive_upgrade_operations(sample) == ["drop_column"]
