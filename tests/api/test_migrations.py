"""Migration hygiene: a fresh baseline and a forward-only upgrade path (CONTRACT I8)."""

from __future__ import annotations

import ast
import re
from decimal import Decimal
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from ezchain_api.db import alembic_config, head_revision
from ezchain_api.models import PaperWalletRow
from ezchain_api.services import paper

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
    assert head_revision() == "0017_ledger_seq_index"


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


def test_paper_wallet_upgrade_attributes_legacy_ledger_rows(tmp_path: Path) -> None:
    """An upgraded SQLite database keeps its cash: legacy rows settle in the genesis wallet."""
    database = tmp_path / "legacy_wallets.db"
    url = f"sqlite+pysqlite:///{database}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE ledger_entries ("
                "id VARCHAR(36) PRIMARY KEY, seq BIGINT, group_id VARCHAR(36), "
                "account VARCHAR(160), asset VARCHAR(160), amount TEXT, usd_value TEXT, "
                "kind VARCHAR(32), created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO ledger_entries VALUES "
                "('legacy-cash', 1, 'genesis', 'cash:USD', 'USD', "
                "'10000', '10000', 'genesis', '2026-01-01 00:00:00'), "
                "('legacy-equity', 2, 'genesis', 'equity:genesis', 'USD', "
                "'-10000', '-10000', 'genesis', '2026-01-01 00:00:00')"
            )
        )
    command.stamp(config, "0014_pair_identity")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        assert "paper_wallets" in inspector.get_table_names()
        assert "wallet_id" in {column["name"] for column in inspector.get_columns("ledger_entries")}
        assert {column["name"] for column in inspector.get_columns("paper_wallets")} == {
            "id",
            "chain",
            "asset",
            "label",
            "created_at",
        }
        with Session(engine) as session:
            # The migration seeds the genesis wallet; boot adds the chain wallets.
            assert session.scalar(select(PaperWalletRow.id)) == paper.SETTLEMENT_WALLET_ID
            paper.ensure_wallets(session)
            wallets = {row["chain"]: row for row in paper.wallets(session)}
            assert wallets["settlement"]["balance_usd"] == "10000"
            assert sum(
                (Decimal(str(row["balance_usd"])) for row in wallets.values()), Decimal(0)
            ) == Decimal("10000")
    finally:
        engine.dispose()


def test_protection_events_migration_creates_the_audit_table(tmp_path: Path) -> None:
    """The upgrade tail builds the table the models expect, on SQLite too."""
    database = tmp_path / "protection.db"
    url = f"sqlite+pysqlite:///{database}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["database_url"] = url
    engine = create_engine(url)
    # 0003 uses Postgres-only DDL; stamp at 0005 and run the tail. Later
    # revisions add columns to ``strategies``, so seed its primary key.
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE strategies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE paper_orders (id VARCHAR(36) PRIMARY KEY, idempotency_key VARCHAR(120))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE market_snapshots (id VARCHAR(36) PRIMARY KEY, chain VARCHAR(40), token_address VARCHAR(160), observed_at DATETIME)"
            )
        )
    command.stamp(config, "0005_price_precision")
    command.upgrade(config, "head")
    try:
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("protection_events")}
        assert columns == {
            "id",
            "arm_id",
            "seq",
            "action",
            "reason",
            "rule_snapshot",
            "mark",
            "observed_at",
            "order_id",
            "created_at",
        }
        unique = {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints("protection_events")
        }
        assert ("uq_protection_event_seq", ("arm_id", "seq")) in unique
        indexes = {index["name"] for index in inspector.get_indexes("protection_events")}
        assert {
            "ix_protection_events_arm_id",
            "ix_protection_events_seq",
            "ix_protection_events_action",
            "ix_protection_events_observed_at",
            "ix_protection_events_order_id",
            "ix_protection_events_created_at",
        } <= indexes
        arm_columns = {column["name"] for column in inspector.get_columns("protection_arms")}
        assert "version" in arm_columns
    finally:
        engine.dispose()
